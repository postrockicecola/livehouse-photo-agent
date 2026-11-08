"""Stage 2 pre-gates (cheap CV) and identical-content suppression.

Livehouse policy: hard-reject only empty / unstructured severe blur / extreme blowout.
Ambiguous concert frames (motion blur, backlight crush, no frontal face, haze, crowd
energy) pass with ``ambiguous_tags`` so Stage3 admission can reserve VLM slots.
"""
from __future__ import annotations

import logging
import hashlib
import threading
import calendar
from datetime import datetime
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import cv2
import numpy as np
from PIL import Image

import imagehash

from engine.operators.image_processor import ImageProcessor
from utils.config_loader import ConfigLoader

logger = logging.getLogger(__name__)


def _configure_opencv_runtime() -> None:
    try:
        cv2.ocl.setUseOpenCL(False)
    except Exception:
        pass
    try:
        cv2.setNumThreads(1)
    except Exception:
        pass


_configure_opencv_runtime()

_face_cascade: Optional[cv2.CascadeClassifier] = None
_face_cascade_init_attempted = False
# OpenCV Haar cascades are not safe for concurrent detectMultiScale on one instance.
_face_cascade_lock = threading.Lock()


def _stage2_norm(tech_score: float, fast_score: float) -> float:
    combined = float(tech_score) * 0.6 + float(fast_score) * 0.4
    return max(0.0, min(1.0, combined / 100.0))


def _get_face_cascade() -> Optional[cv2.CascadeClassifier]:
    global _face_cascade, _face_cascade_init_attempted
    if not _face_cascade_init_attempted:
        _face_cascade_init_attempted = True
        if not hasattr(cv2, "CascadeClassifier") or not hasattr(cv2, "data"):
            logger.warning("Haar cascade unavailable; face filter disabled")
            return None
        path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        cc = cv2.CascadeClassifier(path)
        if cc.empty():
            logger.warning("Haar cascade missing at %s; face filter disabled", path)
        else:
            _face_cascade = cc
    return _face_cascade


def image_dhash_int(img_bgr: np.ndarray) -> int:
    """64-bit difference hash; robust to small crops/rescale for burst-style dupes."""
    if img_bgr is None or img_bgr.size == 0:
        return 0
    g = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    s = cv2.resize(g, (9, 8), interpolation=cv2.INTER_AREA)
    diff = s[:, 1:] > s[:, :-1]
    bits = diff.flatten()
    h = 0
    for i, b in enumerate(bits):
        if b:
            h |= 1 << i
    return int(h)


def image_phash_int(img_bgr: np.ndarray) -> int:
    """
    64-bit perceptual hash (``imagehash.phash``); used for gallery grouping / debug_info.phash.
    Packing order matches flattened ``ImageHash.hash`` for Hamming XOR when grouping images.
    """
    if img_bgr is None or img_bgr.size == 0:
        return 0
    rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    pil = Image.fromarray(rgb)
    h = imagehash.phash(pil)
    bits = np.asarray(h.hash, dtype=np.bool_).flatten()
    val = 0
    for i, b in enumerate(bits):
        if b:
            val |= 1 << i
    return int(val)


def hamming_64(a: int, b: int) -> int:
    return int((int(a) ^ int(b)).bit_count())


def stage2_prefilter_settings(config: Mapping[str, Any]) -> Dict[str, Any]:
    raw = (config.get("processing") or {}).get("stage2_prefilter")
    if not isinstance(raw, dict):
        return {}
    return dict(raw)


def phash_dedup_settings(config: Mapping[str, Any]) -> Dict[str, Any]:
    s = stage2_prefilter_settings(config).get("phash_near_dup")
    if not isinstance(s, dict):
        return {}
    return dict(s)


def need_phash_for_pipeline(config: Mapping[str, Any]) -> bool:
    p = phash_dedup_settings(config)
    return bool(p.get("enabled", False)) or _coverage_admission_enabled(config)


def _coverage_admission_enabled(config: Mapping[str, Any]) -> bool:
    return ((config.get("processing") or {}).get("stage3_gating") or {}).get("strategy") == "coverage_budget"


def read_capture_time(image_path: str) -> float | None:
    """Read EXIF headers only; use a timezone-independent camera clock for burst intervals."""
    try:
        with Image.open(image_path) as image:
            exif = image.getexif()
            camera = exif.get_ifd(34665) if 34665 in exif else {}
            value = camera.get(36867) or exif.get(36867) or exif.get(306)
            if not value:
                return None
            date = datetime.strptime(str(value).strip("\x00 "), "%Y:%m:%d %H:%M:%S")
            subsecond = str(camera.get(37521) or exif.get(37521) or "").strip("\x00 ")
            fraction = float("0." + subsecond) if subsecond.isdigit() else 0.0
            return float(calendar.timegm(date.timetuple())) + fraction
    except Exception:
        # Optional metadata must never reject a decodable image (including malformed EXIF).
        logger.debug("Capture time unavailable for %s", image_path, exc_info=True)
        return None


def row_ambiguous_tags(row: Mapping[str, Any]) -> List[str]:
    """Collect ambiguous tags from a Stage2 eligible row (debug_info or top-level)."""
    dbg = row.get("debug_info") if isinstance(row.get("debug_info"), Mapping) else {}
    raw = dbg.get("ambiguous_tags") if isinstance(dbg, Mapping) else None
    if raw is None:
        raw = row.get("ambiguous_tags")
    if not isinstance(raw, (list, tuple)):
        return []
    out: List[str] = []
    for t in raw:
        s = str(t).strip()
        if s:
            out.append(s)
    return out


@dataclass
class Stage2ImageResult:
    fast_score: float
    phash: int
    prefilter_ok: bool
    prefilter_reason: Optional[str]
    debug_extra: Dict[str, Any]


def _count_faces(gray: np.ndarray, min_neighbors: int) -> int:
    cc = _get_face_cascade()
    if cc is None:
        return -1  # signal: skip face gate
    gray_eq = cv2.equalizeHist(gray)
    with _face_cascade_lock:
        faces = cc.detectMultiScale(
            gray_eq,
            scaleFactor=1.1,
            minNeighbors=max(3, int(min_neighbors)),
            minSize=(48, 48),
        )
    return int(len(faces))


def _bright_subject_cover_frac(gray: np.ndarray) -> float:
    """Fraction of pixels in a high-luminance subject mask (same heuristic as composition)."""
    h, w = gray.shape
    if h < 8 or w < 8:
        return 0.0
    thr = float(np.percentile(gray, 72))
    if thr < 1.0:
        thr = float(np.percentile(gray, 50))
    mask = (gray > thr).astype(np.uint8)
    if int(mask.sum()) < max(64, h * w // 500):
        thr = float(np.percentile(gray, 55))
        mask = (gray > thr).astype(np.uint8)
    return float(mask.mean())


def _structured_motion_escape(blur_type: str, edge_ratio: float) -> bool:
    """Intentional / structured motion should not hard-die on low Laplacian."""
    bt = str(blur_type or "")
    if bt == "artistic_motion_blur":
        return True
    if bt in ("motion_blur", "slight_blur") and float(edge_ratio) >= 0.0035:
        return True
    return False


def assess_stage2_per_image(
    image_path: str,
    config: Mapping[str, Any],
    *,
    tech_score: float,
    debug_info: Mapping[str, Any],
    img_bgr: Optional[np.ndarray] = None,
) -> Stage2ImageResult:
    """
    Single decode when possible: fast aesthetic + optional pHash + prefilter rules.

    Hard rejects are rare (read fail, unstructured severe blur, extreme blowout).
    Softer livehouse defects become ``ambiguous_tags`` and still pass.
    """
    pf = stage2_prefilter_settings(config)
    enabled = bool(pf.get("enabled", False))
    s3c = (config.get("processing") or {}).get("stage3_vlm_cache")
    want_phash_for_s3_cache = isinstance(s3c, dict) and bool(s3c.get("enabled", False))
    want_phash = enabled or need_phash_for_pipeline(config) or want_phash_for_s3_cache

    dbg_x: Dict[str, Any] = {}
    ambiguous: List[str] = []
    pre_reason: Optional[str] = None
    ok = True

    try:
        if img_bgr is None:
            img_bgr, err = ImageProcessor._read_bgr(image_path)
            if img_bgr is None:
                logger.warning("stage2 read failed %s (%s)", image_path, err)
                return Stage2ImageResult(
                    fast_score=0.0,
                    phash=0,
                    prefilter_ok=False,
                    prefilter_reason="read_error",
                    debug_extra={"read_error": True},
                )

        fs = ImageProcessor.fast_aesthetic_assessment(str(image_path), img_bgr=img_bgr)
        ph = image_phash_int(img_bgr) if want_phash else 0
        dbg_x["phash"] = ph
        content = hashlib.sha256(str(img_bgr.shape).encode())
        content.update(memoryview(np.ascontiguousarray(img_bgr)))
        dbg_x["content_digest"] = content.hexdigest()

        q = ConfigLoader.get_quality_thresholds(config)
        analysis_bgr = ImageProcessor.analysis_bgr(img_bgr, q)
        gray = cv2.cvtColor(analysis_bgr, cv2.COLOR_BGR2GRAY)
        dbg_x["brightness"] = float(gray.mean())
        dbg_x["saturation"] = float(cv2.cvtColor(analysis_bgr, cv2.COLOR_BGR2HSV)[:, :, 1].mean())
        if _coverage_admission_enabled(config):
            dbg_x["capture_time"] = read_capture_time(image_path)
            dbg_x["aspect_ratio"] = float(img_bgr.shape[1] / img_bgr.shape[0])

        if not enabled:
            return Stage2ImageResult(
                fast_score=float(fs),
                phash=ph,
                prefilter_ok=True,
                prefilter_reason=None,
                debug_extra=dbg_x,
            )

        bt = str(debug_info.get("blur_type") or "")
        edges = float(debug_info.get("edge_ratio", 0.0) or 0.0)
        contrast = float(debug_info.get("contrast", 0.0) or 0.0)

        lv = debug_info.get("laplacian_var") if debug_info.get("quality_metrics_version") == "reference-scale-v2" else None
        if lv is None:
            noise_sigma = ImageProcessor._noise_sigma(gray)
            lv = max(0.0, ImageProcessor._laplacian_var(gray) - 20.0 * noise_sigma ** 2)
        lv = float(lv)
        dbg_x["stage2_laplacian_var"] = lv

        # Soft blur band (default) vs hard unstructured floor.
        soft_lap = float(pf.get("laplacian_var_min", 40.0) or 0.0)
        hard_lap = float(pf.get("laplacian_var_hard_min", 18.0) or 0.0)
        if hard_lap > 0.0 and lv < hard_lap:
            if (
                _structured_motion_escape(bt, edges)
                or edges >= 0.0012
                or debug_info.get("underexposure_salvage")
                or debug_info.get("stage1_severe_blur_escape")
            ):
                ambiguous.append("soft_blur_structured")
            else:
                ok = False
                pre_reason = f"stage2_blur_laplacian_hard<{hard_lap:.1f}"
        elif soft_lap > 0.0 and lv < soft_lap:
            ambiguous.append("soft_blur")

        if ok and bt == "motion_blur":
            if bool(pf.get("reject_motion_blur", False)):
                ok = False
                pre_reason = "stage2_motion_blur_reject"
            else:
                ambiguous.append("motion_blur")
        elif bt == "artistic_motion_blur":
            ambiguous.append("artistic_motion_blur")

        hf = float(debug_info.get("highlight_frac", 0.0) or 0.0)
        sf = float(debug_info.get("shadow_frac", 0.0) or 0.0)

        # Extreme blowout remains a hard reject; mild crush/blowout → ambiguous.
        hard_hf = pf.get("hard_highlight_frac")
        white_clip = float(debug_info.get("white_clip_frac", hf) or 0.0)
        if ok and hard_hf is not None and white_clip > float(hard_hf):
            ok = False
            pre_reason = "stage2_extreme_overexposure"
        else:
            max_hf = pf.get("max_highlight_frac")
            if max_hf is not None and hf > float(max_hf):
                ambiguous.append("highlight_hot")
            max_sf = pf.get("max_shadow_frac")
            if max_sf is not None and sf > float(max_sf):
                ambiguous.append("shadow_crush")

        # Stage1 underexposure salvage lane → keep visible for Stage3 weighting.
        if bool(debug_info.get("underexposure_salvage")):
            ambiguous.append("underexposure_salvage")

        # Haze / smoke: low contrast + sparse edges — keep, mark for VLM.
        if contrast > 0 and contrast < 12.0 and edges < 0.006:
            ambiguous.append("haze_low_contrast")

        require_face = bool(pf.get("require_face", False))
        face_soft = bool(pf.get("face_soft_gate", True))
        crowd_hard: Optional[int] = None
        crowd_soft: Optional[int] = None
        cm_raw = pf.get("crowd_obstruction_max_faces")
        if cm_raw is not None:
            try:
                crowd_hard = int(cm_raw)
            except (TypeError, ValueError):
                crowd_hard = None
        cs_raw = pf.get("soft_crowd_max_faces")
        if cs_raw is not None:
            try:
                crowd_soft = int(cs_raw)
            except (TypeError, ValueError):
                crowd_soft = None

        need_faces = require_face or face_soft or (
            (crowd_hard is not None and crowd_hard > 0)
            or (crowd_soft is not None and crowd_soft > 0)
        )

        nf = -1
        if ok and need_faces:
            mn = int(pf.get("face_min_neighbors", 5) or 5)
            nf = _count_faces(gray, mn)
            dbg_x["face_count"] = nf
            if nf == -1:
                pass  # cascade missing
            elif nf < 1:
                if require_face:
                    ok = False
                    pre_reason = "stage2_no_face_subject"
                elif face_soft:
                    ambiguous.append("no_face")
            if ok and crowd_hard is not None and crowd_hard > 0 and nf > crowd_hard:
                ok = False
                pre_reason = "stage2_crowd_obstruction"
            elif (
                ok
                and crowd_soft is not None
                and crowd_soft > 0
                and nf >= 0
                and nf > crowd_soft
            ):
                ambiguous.append("crowd_dense")

        if ok:
            cmin = pf.get("composition_min")
            if cmin is not None:
                comp = float(
                    ImageProcessor.assess_composition(str(image_path), gray=gray, img_bgr=img_bgr)
                )
                dbg_x["composition_score"] = comp
                if comp < float(cmin):
                    # Soft only: bright-centroid proxy fails on gel/backlight stages.
                    ambiguous.append("weak_composition_proxy")

        if ok:
            tiny_thr = pf.get("tiny_subject_bright_frac_min")
            if tiny_thr is not None:
                bfrac = _bright_subject_cover_frac(gray)
                dbg_x["bright_subject_frac"] = bfrac
                if bfrac < float(tiny_thr):
                    ambiguous.append("tiny_bright_subject")

        # Deduplicate tags while preserving order.
        seen: set[str] = set()
        amb_uniq: List[str] = []
        for t in ambiguous:
            if t not in seen:
                seen.add(t)
                amb_uniq.append(t)
        if amb_uniq:
            dbg_x["ambiguous_tags"] = amb_uniq

        return Stage2ImageResult(
            fast_score=float(fs),
            phash=ph,
            prefilter_ok=ok,
            prefilter_reason=pre_reason,
            debug_extra=dbg_x,
        )
    except Exception as e:
        logger.warning("assess_stage2_per_image failed for %s: %s", image_path, e)
        return Stage2ImageResult(
            fast_score=40.0,
            phash=0,
            prefilter_ok=False,
            prefilter_reason=f"stage2_prefilter_error:{type(e).__name__}",
            debug_extra={"error": str(e)},
        )


def dedupe_eligible_rows_by_phash(
    rows: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """
    Suppress only identical decoded pixels. pHash remains available for grouping after
    VLM scoring; a similar burst frame can contain the best expression or gesture.
    """
    s = phash_dedup_settings(config)
    if not rows or not bool(s.get("enabled", False)):
        return [dict(r) for r in rows], [], {"enabled": False, "before": len(rows), "after": len(rows)}

    kpc = max(1, int(s.get("keep_per_cluster", 2) or 1))

    scored: List[Tuple[float, Dict[str, Any]]] = []
    for r in rows:
        d = dict(r)
        ts = float(d["tech_score"])
        fs = float(d["fast_score"])
        n = _stage2_norm(ts, fs)
        scored.append((n, d))
    scored.sort(key=lambda x: -x[0])

    clusters: Dict[str, int] = {}
    kept: List[Dict[str, Any]] = []
    removed: List[Dict[str, Any]] = []

    for _norm, r in scored:
        digest = str((r.get("debug_info") or {}).get("content_digest") or "")
        if not digest:
            kept.append(r)
        elif clusters.get(digest, 0) < kpc:
            clusters[digest] = clusters.get(digest, 0) + 1
            kept.append(r)
        else:
            removed.append(r)

    diag = {
        "enabled": True,
        "before": len(rows),
        "after": len(kept),
        "removed_near_dup": len(removed),
        "method": "content_digest",
        "keep_per_cluster": kpc,
    }
    return kept, removed, diag


def log_pipeline_reduction_metrics(
    *,
    stage: str,
    count_in: int,
    count_out: int,
    extra: Optional[Mapping[str, Any]] = None,
) -> None:
    ratio = (1.0 - (count_out / max(1, count_in))) if count_in else 0.0
    payload: Dict[str, Any] = {
        "pipeline_metrics": True,
        "stage": stage,
        "count_in": count_in,
        "count_out": count_out,
        "reduction_ratio": round(ratio, 4),
        "retention_ratio": round(1.0 - ratio, 4),
    }
    if extra:
        payload.update(dict(extra))
    logger.info("%s | %s", stage, payload)
