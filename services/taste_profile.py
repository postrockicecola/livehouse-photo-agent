"""
Learn a session taste vector from user selections (Stage3 dimensions) and re-rank the gallery.

v1: contrastive mean-dim weights (liked vs rest) — no model fine-tuning.
v2 hook: ``few_shot_prompt_block`` for Stage3 re-score jobs (stored exemplars).
Pairwise burst picks: ``services.pairwise_preferences`` (``pairwise_edge_records``, win/loss aggregates)
for Bradley–Terry or ``apply_pairwise_boost_to_metric`` on top of ``personalized_sort_metric``.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Mapping

from services.processor.reporting.audit_io import load_audit_jsonl
from services.result_service import load_raw_results
from utils.gallery_curation import (
    curation_keys_by_verdict,
    curation_liked_keys,
    read_gallery_curation,
)
from utils.runtime_paths import resolve_runtime_file, runtime_dir, runtime_file_path
from utils.stage3_dimensions import STAGE3_DIM_KEYS

logger = logging.getLogger(__name__)

TASTE_PROFILE_FILENAME = "taste_profile.json"
MIN_LIKED_DEFAULT = 5
PERSONALIZE_BLEND = 0.35  # fraction of taste fit mixed into sort key


def taste_profile_path(previews_dir: str | Path) -> Path:
    return runtime_file_path(previews_dir, TASTE_PROFILE_FILENAME)


def _audit_path_for_previews(previews_dir: Path) -> Path | None:
    from utils.config_loader import ConfigLoader

    cfg = ConfigLoader.load()
    log_name = str((cfg.get("paths") or {}).get("log_file") or "aesthetic_audit.jsonl")
    candidates = [
        previews_dir / log_name,
        previews_dir / "aesthetic_audit.jsonl",
        previews_dir.parent / log_name,
        previews_dir.parent / "aesthetic_audit.jsonl",
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


def _dim_vector_from_mapping(dimensions: Mapping[str, Any] | None) -> dict[str, float]:
    dims = dimensions or {}
    out: dict[str, float] = {}
    for k in STAGE3_DIM_KEYS:
        raw = dims.get(k)
        if raw is None:
            continue
        try:
            v = float(raw)
        except (TypeError, ValueError):
            continue
        if v > 10.0:
            v = v / 10.0
        out[k] = max(0.0, min(10.0, v))
    if not out and dims:
        # Legacy 4-dim audit rows
        legacy_map = {
            "subject_clarity": "focus_sharpness",
            "lighting_quality": "light_color_character",
            "atmosphere": "atmosphere_impact",
            "motion_capture": "moment_peak",
        }
        for leg, nk in legacy_map.items():
            if leg in dims:
                try:
                    out[nk] = max(0.0, min(10.0, float(dims[leg])))
                except (TypeError, ValueError):
                    pass
    return out


def _row_file_key(row: Mapping[str, Any]) -> str:
    return str(row.get("file") or Path(str(row.get("path") or "")).name).strip()


def _row_path_key(row: Mapping[str, Any]) -> str:
    p = str(row.get("path") or "").strip().replace("\\", "/")
    return p


def row_matches_curation_key(row: Mapping[str, Any], curation_key: str) -> bool:
    """``gallery_curation`` keys are full paths; legacy keys may be basenames."""
    ck = str(curation_key or "").strip().replace("\\", "/")
    if not ck:
        return False
    path = _row_path_key(row)
    if path and path == ck:
        return True
    fn = _row_file_key(row)
    if fn and fn == ck:
        return True
    if path and ck and (path.endswith("/" + ck) or path.endswith(ck)):
        return True
    return False


def _mean_vector(vectors: list[dict[str, float]]) -> dict[str, float]:
    if not vectors:
        return {k: 0.0 for k in STAGE3_DIM_KEYS}
    acc = {k: 0.0 for k in STAGE3_DIM_KEYS}
    n = 0
    for v in vectors:
        if not v:
            continue
        n += 1
        for k in STAGE3_DIM_KEYS:
            acc[k] += float(v.get(k, 0.0))
    if n == 0:
        return acc
    return {k: acc[k] / n for k in STAGE3_DIM_KEYS}


def rebuild_taste_profile(
    previews_dir: str | Path,
    *,
    min_liked: int = MIN_LIKED_DEFAULT,
) -> dict[str, Any]:
    """
    Build profile from ``gallery_curation.json`` (``liked`` vs rest; tracks reject tags).
    Returns result dict with ``ok``, ``profile`` or ``error``.
    """
    base = Path(previews_dir).expanduser().resolve()
    cur = read_gallery_curation(base)
    liked_keys = curation_liked_keys(cur)
    rejected_keys = curation_keys_by_verdict(cur, "rejected")
    pass_keys = curation_keys_by_verdict(cur, "pass")

    if len(liked_keys) < min_liked:
        return {
            "ok": False,
            "error": "insufficient_liked",
            "min_liked": min_liked,
            "liked_count": len(liked_keys),
        }

    audit_path = _audit_path_for_previews(base)
    audit = load_audit_jsonl(audit_path) if audit_path else {}

    rows = load_raw_results(str(base))
    if not rows:
        return {"ok": False, "error": "no_gallery_rows"}

    liked_vecs: list[dict[str, float]] = []
    rest_vecs: list[dict[str, float]] = []
    rejected_vecs: list[dict[str, float]] = []
    exemplars: list[dict[str, Any]] = []
    like_reason_counts: dict[str, int] = {}
    reject_reason_counts: dict[str, int] = {}

    fb = (cur or {}).get("feedback_by_key") or {}
    for _k, ent in fb.items():
        if not isinstance(ent, dict):
            continue
        for r in ent.get("like_reasons") or []:
            like_reason_counts[str(r)] = like_reason_counts.get(str(r), 0) + 1
        for r in ent.get("reject_reasons") or []:
            reject_reason_counts[str(r)] = reject_reason_counts.get(str(r), 0) + 1

    for row in rows:
        fn = _row_file_key(row)
        if not fn:
            continue
        audit_e = audit.get(fn) or {}
        dims = audit_e.get("dimensions") or row.get("dimensions")
        vec = _dim_vector_from_mapping(dims if isinstance(dims, dict) else None)
        if len(vec) < 2:
            continue
        if any(row_matches_curation_key(row, k) for k in liked_keys):
            liked_vecs.append(vec)
            sc = row.get("overall_score") or (row.get("scores") or {}).get("overall")
            ent = {}
            for lk in liked_keys:
                if row_matches_curation_key(row, lk):
                    raw_ent = fb.get(lk)
                    if isinstance(raw_ent, dict):
                        ent = raw_ent
                    break
            exemplars.append(
                {
                    "file": fn,
                    "overall_score": sc,
                    "dimensions": vec,
                    "like_reasons": list(ent.get("like_reasons") or []),
                }
            )
        elif any(row_matches_curation_key(row, k) for k in rejected_keys):
            rejected_vecs.append(vec)
        else:
            rest_vecs.append(vec)

    if len(liked_vecs) < min_liked:
        return {
            "ok": False,
            "error": "insufficient_liked_with_dimensions",
            "min_liked": min_liked,
            "liked_with_dims": len(liked_vecs),
        }

    if not rest_vecs:
        rest_vecs = liked_vecs

    mean_liked = _mean_vector(liked_vecs)
    mean_rest = _mean_vector(rest_vecs)
    dim_weights: dict[str, float] = {}
    for k in STAGE3_DIM_KEYS:
        dim_weights[k] = round(float(mean_liked.get(k, 0) - mean_rest.get(k, 0)), 4)

    exemplars.sort(
        key=lambda e: float(e.get("overall_score") or 0),
        reverse=True,
    )
    exemplars = exemplars[:12]

    profile = {
        "version": 1,
        "method": "contrastive_dim_mean_v1",
        "n_liked": len(liked_vecs),
        "n_rest": len(rest_vecs),
        "n_rejected": len(rejected_vecs),
        "n_pass": len(pass_keys),
        "mean_liked": mean_liked,
        "mean_rest": mean_rest,
        "dim_weights": dim_weights,
        "like_reason_counts": like_reason_counts,
        "reject_reason_counts": reject_reason_counts,
        "exemplars": exemplars,
        "updated_unix": int(time.time()),
        "audit_path": str(audit_path) if audit_path else None,
    }

    path = taste_profile_path(base)
    try:
        runtime_dir(base, create=True)
        path.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as e:
        logger.warning("write taste_profile failed: %s", e)
        return {"ok": False, "error": "write_failed"}

    return {"ok": True, "profile": profile, "path": str(path)}


def read_taste_profile(previews_dir: str | Path) -> dict[str, Any] | None:
    path = resolve_runtime_file(previews_dir, TASTE_PROFILE_FILENAME)
    if path is None or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def taste_fit_score(entry: Mapping[str, Any], profile: Mapping[str, Any]) -> float:
    """Higher = closer to user's liked dimension pattern (0–10 scale)."""
    weights = profile.get("dim_weights") or {}
    if not isinstance(weights, dict):
        return 0.0
    dims = entry.get("dimensions")
    vec = _dim_vector_from_mapping(dims if isinstance(dims, dict) else None)
    if len(vec) < 2:
        return 5.0
    acc = 0.0
    wsum = 0.0
    for k, w in weights.items():
        if k not in vec:
            continue
        try:
            wf = float(w)
        except (TypeError, ValueError):
            continue
        acc += wf * float(vec[k])
        wsum += abs(wf)
    if wsum <= 0:
        return 0.0
    raw = acc / wsum
    return max(-10.0, min(10.0, raw + 5.0))  # center-ish 0–10


def _base_overall(entry: Mapping[str, Any]) -> float:
    scores = entry.get("scores") or {}
    raw = entry.get("overall_score", scores.get("overall"))
    try:
        v = float(raw)
    except (TypeError, ValueError):
        v = 0.0
    return max(0.0, min(100.0, v))


def personalized_sort_metric(entry: Mapping[str, Any], profile: Mapping[str, Any] | None) -> float:
    base = _base_overall(entry)
    if not profile:
        return base
    fit = taste_fit_score(entry, profile)
    return (1.0 - PERSONALIZE_BLEND) * base + PERSONALIZE_BLEND * (fit * 10.0)


def few_shot_prompt_block(profile: Mapping[str, Any] | None, *, max_examples: int = 6) -> str:
    """Text block for future Stage3 re-score prompts (not wired in v1)."""
    if not profile:
        return ""
    lines = [
        "The photographer's recent selections favor images with these traits (0–10 Stage3 dims):",
    ]
    w = profile.get("dim_weights") or {}
    top = sorted(
        ((k, float(v)) for k, v in w.items() if isinstance(v, (int, float))),
        key=lambda x: -abs(x[1]),
    )[:5]
    for k, delta in top:
        if abs(delta) < 0.15:
            continue
        direction = "higher" if delta > 0 else "lower"
        lines.append(f"- {k}: prefer {direction} than their unselected set (Δ{delta:+.2f})")
    ex = profile.get("exemplars") or []
    if ex:
        lines.append("Representative selected frames (basename):")
        for e in ex[:max_examples]:
            fn = e.get("file")
            if fn:
                lines.append(f"- {fn}")
    return "\n".join(lines)
