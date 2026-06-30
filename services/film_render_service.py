"""On-demand Livehouse film grades (``op_kernel``) for Lab preview — same kernels as ``demo_0414.py``."""
from __future__ import annotations

import hashlib
import os
import secrets
from collections.abc import Callable
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from services.path_service import PathResolver
from services.image_service import read_exif_orientation_tag
from services.optical_params import OpticalConsoleParams
from services.edit_adjustments import EditAdjustments

# VLM-driven per-image grade ("Automated"). Not a fixed preset — params come per request.
AUTOMATED_VARIANT_ID = "film_automated"

FILM_VARIANT_IDS: tuple[str, ...] = (
    "film_livehouse",
    "film_cinestill_800t",
    "film_cinestill_50d",
    "film_cold_v2",
    "film_cold_v3",
    "film_cold_v4",
    "film_black_mist",
    "film_ricoh_gr",
    "film_portra_400",
    "film_gold_200",
    "film_ektar_100",
    "film_fuji_400h",
    "film_fuji_classic_neg",
    "film_hp5_bw",
    "film_tri_x_bw",
    "film_velvia_50",
    "film_superia_400",
    "film_kodachrome_64",
    "film_lomo_xpro",
    "film_ultra_vivid",
    "film_agfa_vista_200",
    "film_astia_100f",
    "film_polaroid_vivid",
    "film_neon_pop",
    "film_teal_magenta",
    "film_sunset_chrome",
    "film_holga_vivid",
    "film_provia_100f",
    "film_dutch_golden",
    "film_aquamarine_pop",
    "film_rose_gold",
    "film_expired_slide",
    "film_candy_chrome",
    "film_neon_tokyo",
    "film_neon_cyan",
    "film_neon_magenta",
    "film_neon_club",
    "film_neon_signage",
    "film_neon_haze",
    "film_mexico_sun",
    "film_spain_passion",
    "film_latin_cinema",
    "film_wong_kar_wai",
    "film_retro_literary_portrait",
)

_RAW_EXT = frozenset({".arw", ".dng", ".cr2", ".cr3", ".nef", ".raf", ".rw2", ".orf"})

# Session export folder names (under ``exported_images/export_<ts>/``).
EXPORT_DIR_JPEG = "jpeg"
EXPORT_DIR_RAW_COPY = "raw"
EXPORT_DIR_GRADED_FROM_RAW = "graded_from_raw"


def path_allowed_for_film_render(abs_path: Path, resolver: PathResolver) -> bool:
    """Allow previews + session tree + RAW (same sources as ``/image`` in practice)."""
    try:
        p = abs_path.expanduser().resolve(strict=False)
    except OSError:
        return False
    if not p.is_file():
        return False

    base = resolver.base_dir.resolve()
    session_dir, raw_hint = resolver.session_and_raw_hint()

    roots: list[Path] = [
        base,
        base.parent.resolve(),
        session_dir.resolve(),
    ]
    if raw_hint:
        try:
            roots.append(raw_hint.resolve())
        except OSError:
            pass
    for extra in (session_dir / "film_results", base.parent / "film_results"):
        try:
            if extra.is_dir():
                roots.append(extra.resolve())
        except OSError:
            pass

    uniq: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        try:
            r = root.resolve(strict=False)
        except OSError:
            continue
        key = str(r)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(r)
        try:
            p.relative_to(r)
            return True
        except ValueError:
            continue

    # Fallback: ``Path.relative_to`` can fail across symlink / mount edge cases; mirror prefix check.
    try:
        p_real = os.path.realpath(p)
    except OSError:
        p_real = os.path.normpath(str(p))
    for r in uniq:
        try:
            r_real = os.path.realpath(r)
        except OSError:
            continue
        if p_real == r_real or p_real.startswith(r_real + os.sep):
            return True
    return False


def _film_work_long_edge(max_side: int, variant_id: str | None = None) -> int:
    """Longest side to grade at before final ``thumbnail``; keeps strip sizes fast without heavy full-res kernels."""
    ms = max(256, min(4096, int(max_side)))
    # Gallery thumbs (≤900): grade closer to output size — much faster, still fine at 720px out.
    if ms <= 900:
        tight = int(round(ms * 1.35))
        return max(ms, min(1080, tight))
    inner = max(ms * 2, 1280)
    cap = 8192 if ms >= 2000 else 3600
    return max(ms, min(cap, inner))


def _downscale_rgb_for_film_work(
    rgb: np.ndarray, max_side: int, variant_id: str | None = None
) -> np.ndarray:
    h, w = rgb.shape[:2]
    lim = _film_work_long_edge(max_side, variant_id)
    long_edge = max(h, w)
    if long_edge <= lim:
        return rgb
    scale = lim / long_edge
    nw = max(1, int(round(w * scale)))
    nh = max(1, int(round(h * scale)))
    return cv2.resize(rgb, (nw, nh), interpolation=cv2.INTER_AREA)


def is_raw_path(path: Path) -> bool:
    return path.suffix.lower() in _RAW_EXT


def resolve_film_catalog_paths(
    resolver: PathResolver,
    image_name: str,
    *,
    explicit_source: Path | None = None,
) -> dict[str, Path | None]:
    """Preview / RAW / optional Lab explicit path for one catalog basename."""
    preview = resolver.resolve_preview(image_name)
    raw = resolver.resolve_raw(image_name)
    explicit: Path | None = None
    if explicit_source is not None:
        try:
            p = explicit_source.expanduser().resolve(strict=False)
            if p.is_file():
                explicit = p
        except OSError:
            explicit = None
    return {"preview": preview, "raw": raw, "explicit": explicit}


def resolve_film_sources_for_export(
    resolver: PathResolver,
    image_name: str,
    *,
    explicit_source: Path | None = None,
) -> list[tuple[Path, str]]:
    """Ordered JPEG/film render candidates: explicit → preview (skip RAW here; use graded_from_raw)."""
    paths = resolve_film_catalog_paths(
        resolver, image_name, explicit_source=explicit_source
    )
    out: list[tuple[Path, str]] = []
    seen: set[str] = set()

    def _add(p: Path | None, tag: str) -> None:
        if p is None or not p.is_file() or is_raw_path(p):
            return
        key = str(p.resolve())
        if key in seen:
            return
        seen.add(key)
        out.append((p, tag))

    _add(paths.get("explicit"), "film_source")
    _add(paths.get("preview"), "preview")
    return out


def load_rgb_u8(image_path: Path) -> np.ndarray:
    ext = image_path.suffix.lower()
    if ext in _RAW_EXT:
        import rawpy  # type: ignore

        with rawpy.imread(str(image_path)) as raw:
            return raw.postprocess(use_camera_wb=True, no_auto_bright=False)

    from services.jpeg_exif_orientation import open_display_ready_image

    im = open_display_ready_image(image_path, 0)
    return np.array(im.convert("RGB"), dtype=np.uint8)


def _apply_variant(
    rgb: np.ndarray,
    variant_id: str,
    *,
    optical: OpticalConsoleParams | None = None,
    adjustments: EditAdjustments | None = None,
) -> np.ndarray:
    from services.optical_params import _strength_multiplier
    from op_kernel import (
        apply_black_mist_film,
        apply_cinestill_800t,
        apply_film_cinestill_50d,
        apply_film_ektar_100,
        apply_film_fuji_400h,
        apply_film_fuji_classic_neg,
        apply_film_gold_200,
        apply_film_hp5_bw,
        apply_film_portra_400,
        apply_film_tri_x_bw,
        apply_film_velvia_50,
        apply_film_superia_400,
        apply_film_kodachrome_64,
        apply_film_lomo_xpro,
        apply_film_ultra_vivid,
        apply_film_agfa_vista_200,
        apply_film_astia_100f,
        apply_film_polaroid_vivid,
        apply_film_neon_pop,
        apply_film_teal_magenta,
        apply_film_sunset_chrome,
        apply_film_holga_vivid,
        apply_film_provia_100f,
        apply_film_dutch_golden,
        apply_film_aquamarine_pop,
        apply_film_rose_gold,
        apply_film_expired_slide,
        apply_film_candy_chrome,
        apply_film_neon_tokyo,
        apply_film_neon_cyan,
        apply_film_neon_magenta,
        apply_film_neon_club,
        apply_film_neon_signage,
        apply_film_neon_haze,
        apply_film_mexico_sun,
        apply_film_spain_passion,
        apply_film_latin_cinema,
        apply_film_wong_kar_wai,
        apply_film_retro_literary_portrait,
        apply_livehouse_cold_film_v2,
        apply_livehouse_cold_film_v3,
        apply_livehouse_cold_film_v4,
        apply_livehouse_film,
        apply_ricoh_gr_positive_film,
    )

    fn_map: dict[str, Callable[[np.ndarray], np.ndarray]] = {
        "film_cinestill_800t": apply_cinestill_800t,
        "film_cinestill_50d": apply_film_cinestill_50d,
        "film_cold_v2": apply_livehouse_cold_film_v2,
        "film_cold_v3": apply_livehouse_cold_film_v3,
        "film_cold_v4": apply_livehouse_cold_film_v4,
        "film_black_mist": apply_black_mist_film,
        "film_ricoh_gr": apply_ricoh_gr_positive_film,
        "film_portra_400": apply_film_portra_400,
        "film_gold_200": apply_film_gold_200,
        "film_ektar_100": apply_film_ektar_100,
        "film_fuji_400h": apply_film_fuji_400h,
        "film_fuji_classic_neg": apply_film_fuji_classic_neg,
        "film_hp5_bw": apply_film_hp5_bw,
        "film_tri_x_bw": apply_film_tri_x_bw,
        "film_velvia_50": apply_film_velvia_50,
        "film_superia_400": apply_film_superia_400,
        "film_kodachrome_64": apply_film_kodachrome_64,
        "film_lomo_xpro": apply_film_lomo_xpro,
        "film_ultra_vivid": apply_film_ultra_vivid,
        "film_agfa_vista_200": apply_film_agfa_vista_200,
        "film_astia_100f": apply_film_astia_100f,
        "film_polaroid_vivid": apply_film_polaroid_vivid,
        "film_neon_pop": apply_film_neon_pop,
        "film_teal_magenta": apply_film_teal_magenta,
        "film_sunset_chrome": apply_film_sunset_chrome,
        "film_holga_vivid": apply_film_holga_vivid,
        "film_provia_100f": apply_film_provia_100f,
        "film_dutch_golden": apply_film_dutch_golden,
        "film_aquamarine_pop": apply_film_aquamarine_pop,
        "film_rose_gold": apply_film_rose_gold,
        "film_expired_slide": apply_film_expired_slide,
        "film_candy_chrome": apply_film_candy_chrome,
        "film_neon_tokyo": apply_film_neon_tokyo,
        "film_neon_cyan": apply_film_neon_cyan,
        "film_neon_magenta": apply_film_neon_magenta,
        "film_neon_club": apply_film_neon_club,
        "film_neon_signage": apply_film_neon_signage,
        "film_neon_haze": apply_film_neon_haze,
        "film_mexico_sun": apply_film_mexico_sun,
        "film_spain_passion": apply_film_spain_passion,
        "film_latin_cinema": apply_film_latin_cinema,
        "film_wong_kar_wai": apply_film_wong_kar_wai,
        "film_retro_literary_portrait": apply_film_retro_literary_portrait,
    }
    fn = fn_map.get(variant_id)
    if variant_id == AUTOMATED_VARIANT_ID:
        from op_kernel import apply_parametric_grade

        out = (
            apply_parametric_grade(rgb, adjustments)
            if adjustments is not None
            else rgb
        )
    elif variant_id == "film_livehouse":
        opt = optical or OpticalConsoleParams()
        bloom = 1.55 * (_strength_multiplier(opt.air, span=1.85) if opt.air > 0 else 1.0)
        hal = 0.95 * (_strength_multiplier(opt.halation, span=1.65) if opt.halation > 0 else 1.0)
        grain = 0.48 * (_strength_multiplier(opt.time, span=2.05) if opt.time > 0 else 1.0)
        out = apply_livehouse_film(
            rgb,
            bloom_strength=bloom,
            halation_strength=hal,
            color_strength=0.94,
            grain_strength=grain,
        )
    elif fn is None:
        raise ValueError(f"unknown film variant: {variant_id}")
    else:
        out = fn(rgb)
    out = np.asarray(out, dtype=np.uint8)
    if optical and optical.is_active():
        from op_kernel import apply_optical_console_enhancements

        baked = variant_id == "film_livehouse"
        out = apply_optical_console_enhancements(
            out,
            air=optical.air,
            halation=optical.halation,
            night=optical.night,
            dream=optical.dream,
            flow=optical.flow,
            time=optical.time,
            wear=optical.wear,
            flow_angle=optical.flow_angle,
            skip_bloom=baked and optical.air > 0,
            skip_halation=baked and optical.halation > 0,
            skip_grain=baked and optical.time > 0,
        )
    return out


def _cache_file_name(
    src: Path,
    variant_id: str,
    rotate: int,
    max_side: int,
    cache_key_extra: str = "displayReady1",
    optical_cache_token: str = "",
    adjust_cache_token: str = "",
) -> str:
    try:
        st = src.stat()
        meta = (
            f"{src.resolve()}|{variant_id}|r={rotate}|m={max_side}|mtime_ns={st.st_mtime_ns}|"
            f"{cache_key_extra}|opt={optical_cache_token}|adj={adjust_cache_token}|p=filmWorkV15"
        )
    except OSError:
        meta = (
            f"{src}|{variant_id}|r={rotate}|m={max_side}|mtime_ns=0|{cache_key_extra}|"
            f"opt={optical_cache_token}|adj={adjust_cache_token}|p=filmWorkV15"
        )
    return hashlib.sha256(meta.encode()).hexdigest() + ".jpg"


def render_film_to_cache(
    *,
    src_path: Path,
    variant_id: str,
    rotate: int,
    max_side: int,
    cache_root: Path,
    cache_key_extra: str = "displayReady1",
    optical: OpticalConsoleParams | None = None,
    adjustments: EditAdjustments | None = None,
) -> Path:
    optical = optical or None
    cache_root.mkdir(parents=True, exist_ok=True)
    out = cache_root / _cache_file_name(
        src_path,
        variant_id,
        rotate,
        max_side,
        cache_key_extra,
        optical_cache_token=optical.cache_token() if optical else "",
        adjust_cache_token=adjustments.cache_token() if adjustments else "",
    )
    if out.is_file():
        return out

    rgb = _downscale_rgb_for_film_work(load_rgb_u8(src_path), max_side, variant_id)
    processed = _apply_variant(rgb, variant_id, optical=optical, adjustments=adjustments)
    im = Image.fromarray(processed)
    jpeg_orient = read_exif_orientation_tag(src_path)
    if rotate and jpeg_orient == 1:
        im = im.rotate(-rotate, expand=True)
    if im.mode != "RGB":
        im = im.convert("RGB")
    im.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)

    tmp = out.with_name(f"{out.stem}.{secrets.token_hex(8)}.tmp.jpg")
    fast_thumb = max_side <= 900
    try:
        im.save(
            tmp,
            format="JPEG",
            quality=84 if fast_thumb else 88,
            optimize=not fast_thumb,
        )
        os.replace(tmp, out)
    except Exception:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise
    return out
