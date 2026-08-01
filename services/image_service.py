"""Image processing service: cache + resize + rotate (+ optional EV display lift)."""
from __future__ import annotations

import hashlib
import io
from pathlib import Path

from PIL import Image, ImageEnhance

from services.jpeg_exif_orientation import open_display_ready_image, read_exif_orientation_tag_for_file

read_exif_orientation_tag = read_exif_orientation_tag_for_file


def _quantize_ev(ev: float) -> float:
    """Stable cache-key / query quantization (matches gallery URL precision)."""
    try:
        v = float(ev)
    except (TypeError, ValueError):
        return 0.0
    if abs(v) < 1e-6:
        return 0.0
    return round(v, 3)


def apply_ev_lift(im: Image.Image, ev: float) -> Image.Image:
    """Multiply channels by ``2**ev`` (same lift as underexposure salvage preview)."""
    q = _quantize_ev(ev)
    if q <= 0.0:
        return im
    return ImageEnhance.Brightness(im).enhance(2.0**q)


class ImageService:
    CACHE_HEADERS = {"Cache-Control": "public, max-age=31536000, immutable"}

    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        from utils.runtime_paths import runtime_dir

        self.cache_dir = runtime_dir(self.base_dir) / "image_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _cache_key(path: Path, rotate: int, max_side: int, ev: float = 0.0) -> str:
        try:
            mtime_ns = path.stat().st_mtime_ns
        except OSError:
            mtime_ns = 0
        ev_q = _quantize_ev(ev)
        key = f"{path.resolve()}|r={rotate}|m={max_side}|ev={ev_q}|t={mtime_ns}|v=displayReady2"
        return hashlib.sha1(key.encode("utf-8")).hexdigest() + ".jpg"

    def resolve_cached_path(
        self, image_path: Path, rotate: int, max_side: int, ev: float = 0.0
    ) -> Path:
        return self.cache_dir / self._cache_key(image_path, rotate, max_side, ev)

    def encode_display_thumbnail(
        self, image_path: Path, rotate: int, max_side: int, ev: float = 0.0
    ) -> bytes | None:
        """Same pixels as cache file, in memory — used when writing cache fails (e.g. read-only)."""
        try:
            im = open_display_ready_image(image_path, rotate)
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            im = apply_ev_lift(im, ev)
            im.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=88, optimize=True)
            return buf.getvalue()
        except Exception:
            return None

    def build_cached_image(
        self, image_path: Path, rotate: int, max_side: int, ev: float = 0.0
    ) -> Path | None:
        cache_path = self.resolve_cached_path(image_path, rotate, max_side, ev)
        if cache_path.exists():
            return cache_path
        try:
            im = open_display_ready_image(image_path, rotate)
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            im = apply_ev_lift(im, ev)
            im.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
            im.save(cache_path, format="JPEG", quality=88, optimize=True)
            return cache_path
        except Exception:
            return None
