"""Normalize model dimension scores (0–10 vs 0–100) for HTML bars and labels."""


def score_to_percent_bar(raw) -> float:
    """
    Return a 0–100 value for progress-bar width and /100 labels.
    VLM outputs 0–10; older or synthetic data may already be 0–100.
    """
    if raw is None:
        return 0.0
    try:
        x = float(raw)
    except (TypeError, ValueError):
        return 0.0
    if x <= 10.0:
        return min(100.0, x * 10.0)
    return min(100.0, x)
