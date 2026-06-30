"""Pure-numpy metrics for Stage3 score evaluation (no scipy/sklearn dependency).

All functions take aligned, equal-length sequences of floats and ignore pairs
where either side is ``None`` / NaN (callers should pre-filter, but we guard too).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np


def _clean_pairs(a: Sequence[float], b: Sequence[float]) -> tuple[np.ndarray, np.ndarray]:
    xa = np.asarray(a, dtype=float)
    xb = np.asarray(b, dtype=float)
    if xa.shape != xb.shape:
        raise ValueError(f"length mismatch: {xa.shape} vs {xb.shape}")
    mask = ~(np.isnan(xa) | np.isnan(xb))
    return xa[mask], xb[mask]


def _rankdata(x: np.ndarray) -> np.ndarray:
    """Average ranks (1-based), ties share the mean rank — matches scipy 'average'."""
    order = np.argsort(x, kind="mergesort")
    ranks = np.empty(len(x), dtype=float)
    ranks[order] = np.arange(1, len(x) + 1, dtype=float)
    # average tied groups
    sx = x[order]
    i = 0
    n = len(sx)
    while i < n:
        j = i + 1
        while j < n and sx[j] == sx[i]:
            j += 1
        if j - i > 1:
            avg = ranks[order[i:j]].mean()
            ranks[order[i:j]] = avg
        i = j
    return ranks


def pearson(a: Sequence[float], b: Sequence[float]) -> float:
    xa, xb = _clean_pairs(a, b)
    if len(xa) < 2 or np.std(xa) == 0 or np.std(xb) == 0:
        return float("nan")
    return float(np.corrcoef(xa, xb)[0, 1])


def spearman(a: Sequence[float], b: Sequence[float]) -> float:
    xa, xb = _clean_pairs(a, b)
    if len(xa) < 2:
        return float("nan")
    ra = _rankdata(xa)
    rb = _rankdata(xb)
    if np.std(ra) == 0 or np.std(rb) == 0:
        return float("nan")
    return float(np.corrcoef(ra, rb)[0, 1])


def mae(a: Sequence[float], b: Sequence[float]) -> float:
    xa, xb = _clean_pairs(a, b)
    if len(xa) == 0:
        return float("nan")
    return float(np.mean(np.abs(xa - xb)))


def rmse(a: Sequence[float], b: Sequence[float]) -> float:
    xa, xb = _clean_pairs(a, b)
    if len(xa) == 0:
        return float("nan")
    return float(np.sqrt(np.mean((xa - xb) ** 2)))


@dataclass
class RankingAtK:
    k: int
    precision: float
    recall: float
    overlap: int
    n_positives: int


def precision_recall_at_k(
    pred_scores: Sequence[float],
    is_positive: Sequence[bool],
    k: int,
) -> RankingAtK:
    """Rank items by ``pred_scores`` desc; measure recovery of human-kept items.

    - precision@k = (# kept among top-k) / k
    - recall@k    = (# kept among top-k) / (total kept)
    """
    scores = np.asarray(pred_scores, dtype=float)
    pos = np.asarray(is_positive, dtype=bool)
    n = len(scores)
    if n == 0 or k <= 0:
        return RankingAtK(k=k, precision=float("nan"), recall=float("nan"), overlap=0, n_positives=int(pos.sum()))
    kk = min(k, n)
    top_idx = np.argsort(-scores, kind="mergesort")[:kk]
    overlap = int(pos[top_idx].sum())
    n_pos = int(pos.sum())
    precision = overlap / kk
    recall = (overlap / n_pos) if n_pos > 0 else float("nan")
    return RankingAtK(k=k, precision=precision, recall=recall, overlap=overlap, n_positives=n_pos)


def group_mean_separation(
    pred_scores: Sequence[float],
    is_positive: Sequence[bool],
) -> tuple[float, float, float]:
    """Mean predicted score for kept vs discarded, and their gap (kept - discarded)."""
    scores = np.asarray(pred_scores, dtype=float)
    pos = np.asarray(is_positive, dtype=bool)
    keep = scores[pos]
    drop = scores[~pos]
    m_keep = float(np.mean(keep)) if len(keep) else float("nan")
    m_drop = float(np.mean(drop)) if len(drop) else float("nan")
    gap = m_keep - m_drop if (len(keep) and len(drop)) else float("nan")
    return m_keep, m_drop, gap


# ---------------------------------------------------------------------------
# Bias / calibration analysis
# ---------------------------------------------------------------------------


def bias_stats(
    human: Sequence[float],
    model: Sequence[float],
) -> dict:
    """Overall bias (model − human) statistics.

    Returns empty dict when fewer than 2 valid pairs exist.
    Fields:
        n               : int   — valid pair count
        mean_bias       : float — mean(model − human); positive = systematic over-scoring
        std_bias        : float — standard deviation of bias
        median_bias     : float — median(model − human)
        pct_overscored  : float — % of photos where model > human
        pct_underscored : float — % of photos where model < human
    """
    xa, xb = _clean_pairs(human, model)
    if len(xa) < 2:
        return {"n": int(len(xa))}
    diff = xb - xa  # model − human
    return {
        "n": int(len(diff)),
        "mean_bias": float(np.mean(diff)),
        "std_bias": float(np.std(diff)),
        "median_bias": float(np.median(diff)),
        "pct_overscored": float(np.mean(diff > 0) * 100),
        "pct_underscored": float(np.mean(diff < 0) * 100),
    }


def quintile_calibration(
    human: Sequence[float],
    model: Sequence[float],
    *,
    n_bins: int = 5,
) -> list[dict]:
    """Bias (model − human) broken down by human-score quintile.

    Reveals systematic over-scoring in the trash tier (model floor) and
    under-scoring in the top tier (model ceiling) — the classic regression
    toward the mean pattern in learned quality assessors.

    Returns a list of dicts (one per quintile), each with:
        quintile     : int         — 1-indexed bin number
        range        : str         — e.g. "65–75"
        n            : int         — sample count
        human_mean   : float       — mean human score in this bin
        model_mean   : float       — mean model score in this bin
        bias_mean    : float       — mean(model − human) in this bin
        bias_std     : float       — std(model − human) in this bin
    """
    xa, xb = _clean_pairs(human, model)
    if len(xa) < n_bins:
        return []

    # Boundaries from human-score quantiles to get equal-population bins.
    bounds = np.quantile(xa, np.linspace(0, 1, n_bins + 1))
    results: list[dict] = []
    for i in range(n_bins):
        lo, hi = bounds[i], bounds[i + 1]
        mask = (xa >= lo) & (xa <= hi) if i == n_bins - 1 else (xa >= lo) & (xa < hi)
        h_q, m_q = xa[mask], xb[mask]
        if len(h_q) == 0:
            continue
        diff = m_q - h_q
        results.append(
            {
                "quintile": i + 1,
                "range": f"{lo:.0f}–{hi:.0f}",
                "n": int(len(h_q)),
                "human_mean": float(np.mean(h_q)),
                "model_mean": float(np.mean(m_q)),
                "bias_mean": float(np.mean(diff)),
                "bias_std": float(np.std(diff)),
            }
        )
    return results
