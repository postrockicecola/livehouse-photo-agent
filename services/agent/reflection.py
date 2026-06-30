"""Reflection: validate a VLM analysis and decide whether to self-correct.

This is the "self-correction" half of the agent loop. After every ANALYZE step the
loop asks :func:`reflect` whether the result is trustworthy. Two outcomes matter:

1. **Structural validity** — does the analysis carry the dimensions/score contract?
   Reusing the Stage3 rubric keys keeps the agent honest about malformed output.
2. **Escalation** — a usable-but-shaky result (low confidence, score sitting in the
   ambiguous keep/discard band, or a degraded/fallback inference) is re-analyzed
   once at the stronger ``escalation_tier``. This is the agent spending more compute
   *only where the decision is hard*, which is the whole point of the loop.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from services.agent.types import AgentConfig, Candidate
from utils.stage3_dimensions import STAGE3_DIM_KEYS


@dataclass
class ReflectionVerdict:
    valid: bool                 # structural validity of the analysis payload
    escalate: bool              # should we re-analyze at a higher tier?
    reason: str = ""


def validate_analysis(analysis: Optional[dict[str, Any]]) -> tuple[bool, str]:
    """Lightweight schema check on a Stage3-like analysis dict."""
    if not analysis:
        return False, "empty analysis"
    if analysis.get("error"):
        return False, f"analysis error: {str(analysis.get('reason') or 'unknown')[:80]}"
    if analysis.get("score") is None:
        return False, "missing score"
    dims = analysis.get("dimensions")
    if dims is not None and isinstance(dims, dict) and dims:
        # full-tier output should expose the full rubric; a near-empty dict is suspect
        present = [k for k in STAGE3_DIM_KEYS if dims.get(k) is not None]
        if 0 < len(present) < len(STAGE3_DIM_KEYS) // 2:
            return True, "sparse dimensions"
    return True, ""


def reflect(cand: Candidate, config: AgentConfig) -> ReflectionVerdict:
    """Decide validity + whether to escalate this candidate one tier up."""
    valid, why = validate_analysis(cand.analysis)
    if not valid:
        # Invalid output is itself a reason to retry at a stronger tier (if allowed).
        return ReflectionVerdict(
            valid=False,
            escalate=bool(config.allow_escalation and not cand.escalated and not _at_top_tier(cand, config)),
            reason=why or "invalid analysis",
        )

    if not config.allow_escalation or cand.escalated or _at_top_tier(cand, config):
        return ReflectionVerdict(valid=True, escalate=False, reason=why)

    conf = cand.confidence
    if conf is not None and conf < config.confidence_floor:
        return ReflectionVerdict(valid=True, escalate=True, reason=f"low confidence {conf:.2f}")

    score = cand.score
    lo, hi = config.ambiguous_band
    if score is not None and lo <= score <= hi:
        return ReflectionVerdict(
            valid=True, escalate=True, reason=f"score {score:.1f} in ambiguous band [{lo:.0f},{hi:.0f}]"
        )

    analysis = cand.analysis or {}
    degraded = bool(analysis.get("inference_degraded")) or (
        (analysis.get("stage3_meta") or {}).get("outcome") == "degraded_inference"
    )
    if degraded:
        return ReflectionVerdict(valid=True, escalate=True, reason="degraded inference")

    if why == "sparse dimensions":
        return ReflectionVerdict(valid=True, escalate=True, reason="sparse dimensions")

    return ReflectionVerdict(valid=True, escalate=False, reason="")


def _at_top_tier(cand: Candidate, config: AgentConfig) -> bool:
    return cand.tier == config.escalation_tier
