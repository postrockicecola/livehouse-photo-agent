"""EXIF display orientation for gallery JPEGs (aligned with ``/image`` output).

1. ``ImageOps.exif_transpose`` (handles XMP / odd containers better than raw ``transpose``).
2. If EXIF says rotate (``resolve`` > 1) but transpose did not change geometry for a non-square
   image, apply the same ``Transpose`` mapping as Pillow's ``exif_transpose`` (nested-IFD case).
3. If Pillow/exiftool still report orientation 1, optional ``extra_rotate_degrees`` (RAW) is applied
   (same sign as ``ImageService.build_cached_image``).
"""
from __future__ import annotations

import logging
import re
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

import os

logger = logging.getLogger(__name__)

from PIL import Image, ImageOps

# Prefer Image.Transpose (Pillow 9+); fall back to legacy int constants.
try:
    _T = Image.Transpose
    _FLIP_LR = _T.FLIP_LEFT_RIGHT
    _ROT180 = _T.ROTATE_180
    _FLIP_TB = _T.FLIP_TOP_BOTTOM
    _TRANSPOSE = _T.TRANSPOSE
    _ROT270 = _T.ROTATE_270
    _TRANSVERSE = _T.TRANSVERSE
    _ROT90 = _T.ROTATE_90
except AttributeError:
    _FLIP_LR = Image.FLIP_LEFT_RIGHT
    _ROT180 = Image.ROTATE_180
    _FLIP_TB = Image.FLIP_TOP_BOTTOM
    _TRANSPOSE = Image.TRANSPOSE
    _ROT270 = Image.ROTATE_270
    _TRANSVERSE = Image.TRANSVERSE
    _ROT90 = Image.ROTATE_90

_EXIF_TO_TRANSPOSE: dict[int, Any] = {
    2: _FLIP_LR,
    3: _ROT180,
    4: _FLIP_TB,
    5: _TRANSPOSE,
    6: _ROT270,
    7: _TRANSVERSE,
    8: _ROT90,
}


def _orientation_from_exiftool(path: str | Path) -> int | None:
    """EXIF Orientation 1–8 via exiftool, or None."""
    try:
        proc = subprocess.run(
            ["exiftool", "-n", "-Orientation", str(path)],
            capture_output=True,
            text=True,
            timeout=4.0,
            check=False,
        )
        if proc.returncode != 0 or not (proc.stdout or "").strip():
            return None
        for line in (proc.stdout or "").splitlines():
            if ":" not in line:
                continue
            tail = line.split(":", 1)[1].strip()
            m = re.match(r"^(-?\d+)", tail)
            if m:
                n = int(m.group(1))
                if 1 <= n <= 8:
                    return n
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    return None


def _exif_orientation_from_pillow(im: Image.Image) -> int:
    im.load()
    exif = im.getexif()
    if exif is None:
        return 1

    def _parse_orientation(raw: object) -> int | None:
        if raw is None:
            return None
        try:
            n = int(raw)
            return n if 1 <= n <= 8 else None
        except (TypeError, ValueError):
            return None

    try:
        from PIL import ExifTags

        o = _parse_orientation(exif.get(getattr(ExifTags.Base, "Orientation", 274), 1))
        if o is not None and o > 1:
            return o
        o = _parse_orientation(exif.get(274) or exif.get(0x0112))
        if o is not None and o > 1:
            return o
        try:
            from PIL.ExifTags import IFD

            for ifd in (IFD.Exif, getattr(IFD, "IFD1", None)):
                if ifd is None:
                    continue
                try:
                    sub = exif.get_ifd(ifd)
                except Exception:
                    continue
                if not sub:
                    continue
                o = _parse_orientation(
                    sub.get(getattr(ExifTags.Base, "Orientation", 274)) or sub.get(274) or sub.get(0x0112)
                )
                if o is not None and o > 1:
                    return o
        except Exception:
            pass
    except Exception:
        pass
    return 1


def resolve_exif_orientation_number(path: str | Path, im: Image.Image) -> int:
    path = Path(path)
    pil_o = _exif_orientation_from_pillow(im)
    if pil_o > 1:
        return pil_o
    xt_o = _orientation_from_exiftool(path)
    if xt_o is not None and xt_o > 1:
        return xt_o
    return 1


def _manual_transpose(im: Image.Image, orientation: int) -> Image.Image:
    if orientation <= 1:
        return im
    method = _EXIF_TO_TRANSPOSE.get(orientation)
    if method is None:
        return im
    return im.transpose(method)


def open_display_ready_image(path: str | Path, extra_rotate_degrees: int = 0) -> Image.Image:
    """
    Open ``path`` and return pixels as shown in the Lab (EXIF + optional RAW ``rotate``).

    ``extra_rotate_degrees`` is applied only when resolved EXIF orientation is 1 (same rule as
    ``ImageService.build_cached_image``).
    """
    path = Path(path)
    with Image.open(path) as im:
        im.load()
        w0, h0 = im.size
        o = resolve_exif_orientation_number(path, im)
        out = ImageOps.exif_transpose(im)
        if o > 1 and out.size == (w0, h0) and w0 != h0:
            out = _manual_transpose(im.copy(), o)
        er = int(extra_rotate_degrees) if extra_rotate_degrees else 0
        if er and o == 1:
            out = out.rotate(-er, expand=True)
        return out


def read_exif_orientation_tag_for_file(path: str | Path) -> int:
    path = Path(path)
    try:
        with Image.open(path) as im:
            return resolve_exif_orientation_number(path, im)
    except Exception:
        xt = _orientation_from_exiftool(path)
        return xt if xt is not None else 1


_RAW_SIBLING_EXTENSIONS = (".ARW", ".arw", ".NEF", ".nef", ".CR3", ".cr3", ".DNG", ".dng")


@lru_cache(maxsize=4096)
def _find_raw_sibling(jpeg_path_str: str) -> str | None:
    """Locate the RAW file a preview JPEG was exported from (same stem, up to 4 dirs up)."""
    src = Path(jpeg_path_str)
    cur = src.parent
    for _ in range(4):
        for d in (cur / "RAW", cur):
            if d.is_dir():
                for ext in _RAW_SIBLING_EXTENSIONS:
                    cand = d / (src.stem + ext)
                    if cand.is_file():
                        return str(cand)
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


@lru_cache(maxsize=4096)
def _raw_orientation_degrees(raw_path_str: str) -> int:
    n = _orientation_from_exiftool(raw_path_str)
    return {6: 90, 8: -90, 3: 180}.get(n or 1, 0)


def resolve_capture_rotation_degrees(jpeg_path: str | Path) -> int:
    """Degrees needed to upright a strip-exported preview whose rotation lives only in the RAW.

    Returns 0 when the JPEG carries its own EXIF orientation (``exif_transpose`` already
    handles it) or when no RAW sibling is found. Mirrors gallery-side ``inject_orientation``
    so VLM / analysis consumers see the same upright pixels as the user.
    """
    p = Path(jpeg_path)
    try:
        if read_exif_orientation_tag_for_file(p) != 1:
            return 0
        raw = _find_raw_sibling(str(p))
        if not raw:
            return 0
        return _raw_orientation_degrees(raw)
    except Exception:
        logger.warning("resolve_capture_rotation_degrees failed for %s", p, exc_info=True)
        return 0


def sync_gallery_entry_display_dimensions(entry: dict) -> None:
    """Set ``width`` / ``height`` / ``orientation`` on ``entry`` to match ``open_display_ready_image``."""
    path = entry.get("path")
    if not path or not os.path.isfile(path):
        return
    try:
        rot = int(entry.get("rotate_degrees") or 0)
    except (TypeError, ValueError):
        rot = 0
    try:
        out = open_display_ready_image(path, rot)
        w, h = out.size
        entry["width"] = int(w)
        entry["height"] = int(h)
        entry["orientation"] = "portrait" if h > w else ("landscape" if w > h else "square")
    except Exception:
        logger.warning("sync_gallery_entry_display_dimensions failed for %s", path, exc_info=True)
