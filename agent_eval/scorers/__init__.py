"""Scorers used by the production Agent evaluation harness."""

from agent_eval.scorers.pipeline_quality import (
    binary_metrics,
    build_fixed_packs,
    evaluate_agent_selection,
    evaluate_pipeline,
    score_ranked_selection,
    wilson_interval,
)
from agent_eval.scorers.selection_scorer import (
    DefectEvalResult,
    DefectViolation,
    DefectZeroToleranceEvaluator,
    SelectionMetrics,
    SelectionScorer,
    extract_selected_photo_ids,
)

__all__ = [
    "binary_metrics",
    "build_fixed_packs",
    "DefectEvalResult",
    "DefectViolation",
    "DefectZeroToleranceEvaluator",
    "SelectionMetrics",
    "SelectionScorer",
    "extract_selected_photo_ids",
    "evaluate_agent_selection",
    "evaluate_pipeline",
    "score_ranked_selection",
    "wilson_interval",
]
