"""Unified Stage 3 semantic payload (fast vs full inference) shared by pipeline and UI."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal, Mapping, MutableMapping, Optional

from utils.stage3_dimensions import STAGE3_DIM_KEYS

Stage3Mode = Literal["fast", "full"]


@dataclass(frozen=True)
class Stage3Result:
    score: float
    verdict: str
    dimensions: dict[str, Optional[int]]
    confidence: float
    mode: Stage3Mode


def empty_dimension_slots_none() -> dict[str, Optional[int]]:
    """All rubric slots present; fast path leaves values as None."""
    return {k: None for k in STAGE3_DIM_KEYS}


def calibrated_dimensions_to_int_slots(cal: Mapping[str, Any]) -> dict[str, Optional[int]]:
    """Map calibrated float dimensions (or missing keys) onto fixed int slots."""
    out = empty_dimension_slots_none()
    for k in STAGE3_DIM_KEYS:
        raw = cal.get(k)
        if raw is None:
            out[k] = None
            continue
        try:
            x = float(raw)
        except (TypeError, ValueError):
            out[k] = None
            continue
        ri = int(round(max(0.0, min(10.0, x))))
        out[k] = ri
    return out


def fast_confidence(*, inference_degraded: bool) -> float:
    if inference_degraded:
        return 0.62
    return 0.70


def full_confidence(*, inference_degraded: bool, used_fallback_defaults: bool) -> float:
    if used_fallback_defaults:
        return 0.72
    if inference_degraded:
        return 0.84
    return 0.92


def stage3_result_to_dict(sr: Stage3Result) -> dict[str, Any]:
    """JSON-serializable dict (TypedDict-compatible shape)."""
    d = asdict(sr)
    return d


def fast_stage3_result(
    *,
    score: float,
    verdict: str,
    inference_degraded: bool,
) -> Stage3Result:
    return Stage3Result(
        score=float(score),
        verdict=(verdict or "").strip(),
        dimensions=empty_dimension_slots_none(),
        confidence=fast_confidence(inference_degraded=inference_degraded),
        mode="fast",
    )


def full_stage3_result(
    *,
    score: float,
    verdict: str,
    dimensions_cal: Mapping[str, Any],
    inference_degraded: bool,
    used_fallback_defaults: bool,
) -> Stage3Result:
    return Stage3Result(
        score=float(score),
        verdict=(verdict or "").strip(),
        dimensions=calibrated_dimensions_to_int_slots(dimensions_cal),
        confidence=full_confidence(
            inference_degraded=inference_degraded,
            used_fallback_defaults=used_fallback_defaults,
        ),
        mode="full",
    )


def attach_stage3_result(out: MutableMapping[str, Any], sr: Stage3Result) -> None:
    out["stage3_result"] = stage3_result_to_dict(sr)


def apply_blended_score_to_stage3_result(out: MutableMapping[str, Any], blended: float) -> None:
    """After technical+AI merge, keep ``stage3_result.score`` aligned with ``out['score']``."""
    raw = out.get("stage3_result")
    if not isinstance(raw, dict):
        return
    sr = dict(raw)
    sr["score"] = float(blended)
    out["stage3_result"] = sr


def assert_stage3_result_consistent(out: Mapping[str, Any]) -> None:
    """
    Validate unified schema on pipeline dicts that completed Stage3 inference.
    Skip when ``error`` is true (legacy / fallback shapes).
    """
    if out.get("error"):
        return
    sr = out.get("stage3_result")
    assert isinstance(sr, dict), "stage3_result must be a dict when error is false"
    assert set(sr.keys()) == {"score", "verdict", "dimensions", "confidence", "mode"}, sr.keys()
    assert sr["mode"] in ("fast", "full"), sr["mode"]
    assert isinstance(sr["verdict"], str)
    assert isinstance(sr["dimensions"], dict)
    for k in STAGE3_DIM_KEYS:
        assert k in sr["dimensions"], f"missing dimension key {k}"
        v = sr["dimensions"][k]
        assert v is None or isinstance(v, int), f"bad dimension value {k}={v!r}"
    assert isinstance(sr["confidence"], (int, float))
    assert 0.0 <= float(sr["confidence"]) <= 1.0
    assert isinstance(sr["score"], (int, float))


def reconcile_stage3_result_from_legacy(out: MutableMapping[str, Any]) -> None:
    """
    Best-effort fill when older cache rows lack ``stage3_result`` (keeps merge_vlm valid).
    """
    if out.get("error") or out.get("stage3_result"):
        return
    dims = out.get("dimensions") or {}
    sm = out.get("stage3_meta") or {}
    prompt = str(sm.get("prompt_profile") or "")
    looks_full = (
        prompt.startswith("full-")
        or (isinstance(dims, dict) and any(dims.get(k) is not None for k in STAGE3_DIM_KEYS))
    )
    verdict = (
        str(out.get("verdict") or "").strip()
        or str(out.get("reason") or "").strip()
    )
    raw_score = out.get("score")
    try:
        sc = float(raw_score if raw_score is not None else 0.0)
    except (TypeError, ValueError):
        sc = 0.0
    if looks_full:
        attach_stage3_result(
            out,
            full_stage3_result(
                score=sc,
                verdict=verdict,
                dimensions_cal=dims if isinstance(dims, dict) else {},
                inference_degraded=bool(out.get("inference_degraded"))
                or str(sm.get("outcome") or "") == "degraded_inference",
                used_fallback_defaults=str(sm.get("outcome") or "") == "fallback_defaults",
            ),
        )
    else:
        attach_stage3_result(
            out,
            fast_stage3_result(
                score=sc,
                verdict=verdict,
                inference_degraded=bool(out.get("inference_degraded"))
                or str(sm.get("outcome") or "") == "degraded_inference",
            ),
        )
