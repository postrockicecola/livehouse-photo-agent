"""
Consume pairwise preference logs for taste / personalized ranking (v1 hooks).

Storage lives in ``utils.pairwise_preferences``; this module exposes shapes ranking code expects.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from utils.pairwise_preferences import list_pairwise_entries, read_pairwise_preferences


def pairwise_edge_records(previews_dir: str | Path) -> list[dict[str, Any]]:
    """
    Flat edges for Bradley–Terry / logistic rankers or contrastive extensions.

    Each record: ``winner_key``, ``loser_key``, ``group_id``, ``reason_tags``, ``weight``.
    """
    doc = read_pairwise_preferences(previews_dir)
    if not doc:
        return []
    out: list[dict[str, Any]] = []
    for e in doc.get("entries") or []:
        if not isinstance(e, dict):
            continue
        tags = e.get("reason_tags") or []
        weight = 1.0 + min(0.5, 0.1 * len(tags))  # light boost when user tagged why
        out.append(
            {
                "winner_key": e.get("winner_key"),
                "loser_key": e.get("loser_key"),
                "group_id": e.get("group_id"),
                "reason_tags": list(tags),
                "created_unix": e.get("created_unix"),
                "source": e.get("source"),
                "weight": weight,
            }
        )
    return out


def pairwise_stats(previews_dir: str | Path) -> dict[str, Any]:
    edges = pairwise_edge_records(previews_dir)
    by_group: dict[str | None, int] = {}
    for e in edges:
        g = e.get("group_id")
        by_group[g] = by_group.get(g, 0) + 1
    return {
        "edge_count": len(edges),
        "burst_groups": sum(1 for g in by_group if g is not None),
        "ungrouped_edges": by_group.get(None, 0),
    }


def pairwise_reason_tag_counts(previews_dir: str | Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for e in list_pairwise_entries(previews_dir):
        for t in e.get("reason_tags") or []:
            s = str(t)
            counts[s] = counts.get(s, 0) + 1
    return counts


def apply_pairwise_boost_to_metric(
    row: Mapping[str, Any],
    *,
    win_counts: Mapping[str, int],
    loss_counts: Mapping[str, int],
    boost_per_win: float = 2.0,
    penalty_per_loss: float = 1.5,
) -> float:
    """
    Optional v2 sort hook: adjust a base metric using aggregated win/loss keys.

    Keys should match gallery row path or file (same convention as ``row_matches_curation_key``).
    """
    from services.taste_profile import _row_file_key, _row_path_key

    keys = {_row_path_key(row), _row_file_key(row)}
    keys = {k for k in keys if k}
    delta = 0.0
    for k in keys:
        delta += boost_per_win * float(win_counts.get(k, 0))
        delta -= penalty_per_loss * float(loss_counts.get(k, 0))
    return delta


def aggregate_win_loss_keys(previews_dir: str | Path) -> tuple[dict[str, int], dict[str, int]]:
    wins: dict[str, int] = {}
    losses: dict[str, int] = {}
    for e in pairwise_edge_records(previews_dir):
        w = str(e.get("winner_key") or "").strip()
        l = str(e.get("loser_key") or "").strip()
        if w:
            wins[w] = wins.get(w, 0) + 1
        if l:
            losses[l] = losses.get(l, 0) + 1
    return wins, losses
