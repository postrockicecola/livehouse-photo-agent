"""Aggressive Stage 2 pre-gates (cheap CV) and near-duplicate suppression (perceptual pHash).

Prefer false negatives before expensive VLM. Optional via ``processing.stage2_prefilter`` in config.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import cv2
import numpy as np
from PIL import Image

import imagehash

from engine.operators.image_processor import ImageProcessor

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
    64-bit perceptual hash (``imagehash.phash``); used for Stage3 VLM dedup / debug_info.phash.
    Packing order matches flattened ``ImageHash.hash`` for Hamming XOR with cached entries.
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
    return bool(p.get("enabled", False))


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


def assess_stage2_per_image(
    image_path: str,
    config: Mapping[str, Any],
    *,
    tech_score: float,
    debug_info: Mapping[str, Any],
    img_bgr: Optional[np.ndarray] = None,
) -> Stage2ImageResult:
    """
    Single decode when possible: fast aesthetic + optional pHash (Stage3/cache / dedup) + aggressive prefilter rules.
    """
    pf = stage2_prefilter_settings(config)
    enabled = bool(pf.get("enabled", False))
    s3c = (config.get("processing") or {}).get("stage3_vlm_cache")
    want_phash_for_s3_cache = isinstance(s3c, dict) and bool(s3c.get("enabled", False))
    want_phash = enabled or need_phash_for_pipeline(config) or want_phash_for_s3_cache

    dbg_x: Dict[str, Any] = {}
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

        if not enabled:
            return Stage2ImageResult(
                fast_score=float(fs),
                phash=ph,
                prefilter_ok=True,
                prefilter_reason=None,
                debug_extra=dbg_x,
            )

        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        lap_min = float(pf.get("laplacian_var_min", 0.0) or 0.0)
        if lap_min > 0.0:
            lv = float(ImageProcessor._laplacian_var(gray))
            dbg_x["stage2_laplacian_var"] = lv
            if lv < lap_min:
                ok = False
                pre_reason = f"stage2_blur_laplacian<{lap_min:.1f}"

        if ok and bool(pf.get("reject_motion_blur", True)):
            bt = str(debug_info.get("blur_type") or "")
            if bt == "motion_blur":
                ok = False
                pre_reason = "stage2_motion_blur_reject"

        hf = float(debug_info.get("highlight_frac", 0.0) or 0.0)
        sf = float(debug_info.get("shadow_frac", 0.0) or 0.0)

        if ok:
            max_hf = pf.get("max_highlight_frac")
            max_sf = pf.get("max_shadow_frac")
            if max_hf is not None and hf > float(max_hf):
                ok = False
                pre_reason = "stage2_overexposed"
            elif max_sf is not None and sf > float(max_sf):
                ok = False
                pre_reason = "stage2_underexposed"

        if ok:
            hard_hf = pf.get("hard_highlight_frac")
            if hard_hf is not None and hf > float(hard_hf):
                ok = False
                pre_reason = "stage2_extreme_overexposure"

        require_face = bool(pf.get("require_face", False))
        crowd_cap: Optional[int] = None
        cm_raw = pf.get("crowd_obstruction_max_faces")
        if cm_raw is not None:
            try:
                crowd_cap = int(cm_raw)
            except (TypeError, ValueError):
                crowd_cap = None
        need_faces = require_face or (crowd_cap is not None and crowd_cap > 0)

        nf = -1
        if ok and need_faces:
            mn = int(pf.get("face_min_neighbors", 5) or 5)
            nf = _count_faces(gray, mn)
            dbg_x["face_count"] = nf
            if require_face:
                if nf == -1:
                    pass  # cascade missing
                elif nf < 1:
                    ok = False
                    pre_reason = "stage2_no_face_subject"
            if ok and crowd_cap is not None and crowd_cap > 0 and nf >= 0 and nf > crowd_cap:
                ok = False
                pre_reason = "stage2_crowd_obstruction"

        if ok:
            cmin = pf.get("composition_min")
            if cmin is not None:
                comp = float(ImageProcessor.assess_composition(str(image_path), gray=gray, img_bgr=img_bgr))
                dbg_x["composition_score"] = comp
                if comp < float(cmin):
                    ok = False
                    pre_reason = "stage2_extreme_clipping_or_framing"

        if ok:
            tiny_thr = pf.get("tiny_subject_bright_frac_min")
            if tiny_thr is not None:
                bfrac = _bright_subject_cover_frac(gray)
                dbg_x["bright_subject_frac"] = bfrac
                if bfrac < float(tiny_thr):
                    ok = False
                    pre_reason = "stage2_tiny_subject"

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
    Greedy near-duplicate suppression: process rows best-first (Stage2 norm), keep up to
    ``keep_per_cluster`` images within ``max_hamming`` of a cluster representative.
    """
    s = phash_dedup_settings(config)
    if not rows or not bool(s.get("enabled", False)):
        return [dict(r) for r in rows], [], {"enabled": False, "before": len(rows), "after": len(rows)}

    max_h = int(s.get("max_hamming", 10) or 10)
    kpc = max(1, int(s.get("keep_per_cluster", 2) or 1))

    scored: List[Tuple[float, Dict[str, Any]]] = []
    for r in rows:
        d = dict(r)
        ts = float(d["tech_score"])
        fs = float(d["fast_score"])
        n = _stage2_norm(ts, fs)
        scored.append((n, d))
    scored.sort(key=lambda x: -x[0])

    clusters: List[Dict[str, Any]] = []
    kept: List[Dict[str, Any]] = []
    removed: List[Dict[str, Any]] = []

    for _norm, r in scored:
        ph = int(r.get("phash", 0) or 0)
        found = -1
        for i, c in enumerate(clusters):
            if hamming_64(ph, int(c["hash"])) <= max_h:
                found = i
                break
        if found < 0:
            clusters.append({"hash": ph, "count": 1})
            kept.append(r)
        else:
            c = clusters[found]
            if int(c["count"]) < kpc:
                c["count"] += 1
                kept.append(r)
            else:
                removed.append(r)

    diag = {
        "enabled": True,
        "before": len(rows),
        "after": len(kept),
        "removed_near_dup": len(removed),
        "max_hamming": max_h,
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
