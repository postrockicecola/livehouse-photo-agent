"""
RLHF utilities: pairwise vote storage and Bradley-Terry reward model.

Bradley-Terry model
-------------------
Given pairwise comparisons (i beats j), estimate a latent quality score s_i for
each item such that P(i beats j) = s_i / (s_i + s_j).

We use the standard iterative MM (minorization-maximisation) algorithm:

    s_i(t+1) = W_i / sum_{j != i}  n_ij / (s_i(t) + s_j(t))

where W_i = number of wins by i, n_ij = total comparisons between i and j.
Scores are normalised to [0, 1] after convergence (divide by max).
"""
from __future__ import annotations

import json
import math
import os
import random
import sqlite3
import time
from collections import defaultdict
from typing import Any

# ---------------------------------------------------------------------------
# Vote CRUD
# ---------------------------------------------------------------------------

def record_vote(
    conn: sqlite3.Connection,
    *,
    winner_path: str,
    loser_path: str,
    session_key: str | None = None,
    source: str = "manual",
    voter_id: str | None = None,
) -> int:
    """Insert one pairwise vote; return new row id."""
    cur = conn.execute(
        """
        INSERT INTO rlhf_votes (winner_path, loser_path, session_key, source, voter_id)
        VALUES (?, ?, ?, ?, ?)
        """,
        (winner_path, loser_path, session_key, source, voter_id),
    )
    conn.commit()
    return cur.lastrowid  # type: ignore[return-value]


def get_vote_count(conn: sqlite3.Connection, *, session_key: str | None = None) -> int:
    if session_key:
        row = conn.execute(
            "SELECT COUNT(*) FROM rlhf_votes WHERE session_key = ?", (session_key,)
        ).fetchone()
    else:
        row = conn.execute("SELECT COUNT(*) FROM rlhf_votes").fetchone()
    return int(row[0])


def _path_exists(p: Any) -> bool:
    try:
        return bool(p) and os.path.isfile(str(p))
    except OSError:
        return False


def load_catalog_image_paths(analysis_results_path: str, *, limit: int = 800) -> list[str]:
    """Existing image paths from a gallery ``analysis_results.json`` (the live catalog).

    Used to bootstrap comparison pairs from images that actually exist on this host, so the
    voting UI can render thumbnails even before any votes reference local files.
    """
    try:
        with open(analysis_results_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return []
    items = data if isinstance(data, list) else (data.get("items") or data.get("results") or [])
    out: list[str] = []
    for x in items:
        if not isinstance(x, dict):
            continue
        p = x.get("path") or x.get("image_path") or x.get("file") or x.get("filename")
        if _path_exists(p):
            out.append(str(p))
        if len(out) >= limit:
            break
    return out


def _distinct_voted_paths(conn: sqlite3.Connection, session_key: str | None) -> list[str]:
    if session_key:
        rows = conn.execute(
            """
            SELECT DISTINCT path FROM (
              SELECT winner_path AS path FROM rlhf_votes WHERE session_key = ?
              UNION
              SELECT loser_path  AS path FROM rlhf_votes WHERE session_key = ?
            )
            """,
            (session_key, session_key),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT DISTINCT path FROM (
              SELECT winner_path AS path FROM rlhf_votes
              UNION
              SELECT loser_path  AS path FROM rlhf_votes
            )
            """
        ).fetchall()
    return [r[0] for r in rows]


def get_candidate_pair(
    conn: sqlite3.Connection,
    *,
    session_key: str | None = None,
    fallback_paths: list[str] | None = None,
) -> tuple[str, str] | None:
    """
    Return a random pair (path_a, path_b) of images that **exist on disk** to compare.

    Pool = previously-voted images that still exist + an optional ``fallback_paths`` catalog
    (e.g. the live gallery). Filtering to existing files means a relocated DB (votes pointing
    at another host's paths) still yields renderable pairs. Returns None when <2 candidates.
    """
    pool: list[str] = [p for p in _distinct_voted_paths(conn, session_key) if _path_exists(p)]
    seen = set(pool)
    for p in fallback_paths or []:
        if p not in seen and _path_exists(p):
            pool.append(p)
            seen.add(p)
    if len(pool) < 2:
        return None
    a, b = random.sample(pool, 2)
    return (a, b)


# ---------------------------------------------------------------------------
# Bradley-Terry model fitting (iterative MM algorithm)
# ---------------------------------------------------------------------------

def _fetch_comparisons(
    conn: sqlite3.Connection, session_key: str | None
) -> list[tuple[str, str]]:
    if session_key:
        rows = conn.execute(
            "SELECT winner_path, loser_path FROM rlhf_votes WHERE session_key = ?",
            (session_key,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT winner_path, loser_path FROM rlhf_votes"
        ).fetchall()
    return [(r[0], r[1]) for r in rows]


def compute_bradley_terry(
    conn: sqlite3.Connection,
    *,
    session_key: str | None = None,
    max_iter: int = 200,
    tol: float = 1e-6,
) -> list[dict[str, Any]]:
    """
    Fit a Bradley-Terry model on pairwise votes and return a ranked list.

    Returns
    -------
    list of dicts, sorted by bt_score descending::

        [{"path": ..., "bt_score": 0.0–1.0, "wins": int, "losses": int,
          "comparisons": int, "rank": int}, ...]

    Empty list when fewer than 2 distinct images have been compared.
    """
    comparisons = _fetch_comparisons(conn, session_key)
    if not comparisons:
        return []

    # Collect items and counts
    items: set[str] = set()
    wins: dict[str, int] = defaultdict(int)
    pair_counts: dict[tuple[str, str], int] = defaultdict(int)

    for w, l in comparisons:
        items.add(w)
        items.add(l)
        wins[w] += 1
        key = (min(w, l), max(w, l))
        pair_counts[key] += 1

    item_list = sorted(items)
    n = len(item_list)
    if n < 2:
        return []

    idx = {item: i for i, item in enumerate(item_list)}
    s = [1.0] * n  # initial scores

    # Pre-compute: for each item i, list of (j_idx, n_ij)
    pairs_for: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for (a, b), cnt in pair_counts.items():
        ia, ib = idx[a], idx[b]
        pairs_for[ia].append((ib, cnt))
        pairs_for[ib].append((ia, cnt))

    W = [wins.get(item, 0) for item in item_list]

    for _ in range(max_iter):
        s_new = [0.0] * n
        for i in range(n):
            if W[i] == 0:
                s_new[i] = s[i] * 0.5  # no wins: decay toward 0
                continue
            denom = sum(cnt / (s[i] + s[j]) for j, cnt in pairs_for[i])
            s_new[i] = W[i] / denom if denom > 0 else s[i]

        # Normalise to prevent numerical drift
        total = sum(s_new) or 1.0
        s_new = [v / total * n for v in s_new]

        delta = max(abs(s_new[i] - s[i]) for i in range(n))
        s = s_new
        if delta < tol:
            break

    max_s = max(s) or 1.0
    losses: dict[str, int] = defaultdict(int)
    for _, l in comparisons:
        losses[l] += 1

    results = []
    for i, item in enumerate(item_list):
        w = wins.get(item, 0)
        lo = losses.get(item, 0)
        results.append(
            {
                "path": item,
                "bt_score": round(s[i] / max_s, 4),
                "wins": w,
                "losses": lo,
                "comparisons": w + lo,
            }
        )

    results.sort(key=lambda x: x["bt_score"], reverse=True)
    for rank, r in enumerate(results, 1):
        r["rank"] = rank
    return results


# ---------------------------------------------------------------------------
# Prompt variant registry
# ---------------------------------------------------------------------------

def upsert_prompt_variant(
    conn: sqlite3.Connection,
    *,
    name: str,
    prompt_text: str,
    description: str = "",
    variant_tag: str = "control",
    config_json: str | None = None,
    active: bool = True,
) -> int:
    """Insert or replace a prompt variant; return its id."""
    row = conn.execute(
        "SELECT id FROM prompt_variants WHERE name = ?", (name,)
    ).fetchone()
    if row:
        conn.execute(
            """
            UPDATE prompt_variants
               SET prompt_text = ?, description = ?, variant_tag = ?,
                   config_json = ?, active = ?
             WHERE name = ?
            """,
            (prompt_text, description, variant_tag, config_json, int(active), name),
        )
        conn.commit()
        return int(row[0])
    cur = conn.execute(
        """
        INSERT INTO prompt_variants (name, description, prompt_text, variant_tag, config_json, active)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (name, description, prompt_text, variant_tag, config_json, int(active)),
    )
    conn.commit()
    return cur.lastrowid  # type: ignore[return-value]


def list_prompt_variants(
    conn: sqlite3.Connection, *, active_only: bool = True
) -> list[dict[str, Any]]:
    q = "SELECT * FROM prompt_variants"
    if active_only:
        q += " WHERE active = 1"
    q += " ORDER BY variant_tag, name"
    rows = conn.execute(q).fetchall()
    return [dict(r) for r in rows]


def record_experiment_run(
    conn: sqlite3.Connection,
    *,
    variant_id: int,
    experiment_name: str = "default",
    model_run_id: int | None = None,
    image_path: str | None = None,
    vlm_score: float | None = None,
    outcome: str | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    latency_ms: int | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO prompt_experiment_runs
          (model_run_id, variant_id, experiment_name, image_path,
           vlm_score, outcome, prompt_tokens, completion_tokens, latency_ms)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            model_run_id,
            variant_id,
            experiment_name,
            image_path,
            vlm_score,
            outcome,
            prompt_tokens,
            completion_tokens,
            latency_ms,
        ),
    )
    conn.commit()
    return cur.lastrowid  # type: ignore[return-value]


def summarize_experiment(
    conn: sqlite3.Connection,
    *,
    experiment_name: str = "default",
    since_ts: int | None = None,
) -> list[dict[str, Any]]:
    """
    Per-variant aggregate stats for an experiment.

    Returns list of dicts, one per variant, with keys:
    variant_id, variant_name, variant_tag, runs, avg_score, p25_score, p75_score,
    avg_latency_ms, avg_prompt_tokens, avg_completion_tokens, win_rate_vs_control.
    """
    where = "r.experiment_name = ?"
    params: list[Any] = [experiment_name]
    if since_ts is not None:
        where += " AND r.created_at >= ?"
        params.append(since_ts)

    rows = conn.execute(
        f"""
        SELECT
          v.id            AS variant_id,
          v.name          AS variant_name,
          v.variant_tag   AS variant_tag,
          COUNT(*)        AS runs,
          AVG(r.vlm_score)          AS avg_score,
          AVG(r.latency_ms)         AS avg_latency_ms,
          AVG(r.prompt_tokens)      AS avg_prompt_tokens,
          AVG(r.completion_tokens)  AS avg_completion_tokens,
          SUM(CASE WHEN r.vlm_score IS NOT NULL THEN 1 ELSE 0 END) AS runs_with_score
        FROM prompt_experiment_runs r
        JOIN prompt_variants v ON v.id = r.variant_id
        WHERE {where}
        GROUP BY v.id
        ORDER BY avg_score DESC NULLS LAST
        """,
        params,
    ).fetchall()

    result = []
    for r in rows:
        d = dict(r)
        # round floats for JSON
        for k in ("avg_score", "avg_latency_ms", "avg_prompt_tokens", "avg_completion_tokens"):
            if d.get(k) is not None:
                d[k] = round(float(d[k]), 2)
        result.append(d)

    # Compute win_rate_vs_control (fraction of runs with score > control avg)
    control_avg = next(
        (r["avg_score"] for r in result if r["variant_tag"] == "control"), None
    )
    for r in result:
        if control_avg is not None and r.get("avg_score") is not None:
            # win_rate: share of runs that beat control avg
            v_rows = conn.execute(
                f"""
                SELECT vlm_score FROM prompt_experiment_runs r
                WHERE r.experiment_name = ? AND r.variant_id = ?
                  AND r.vlm_score IS NOT NULL
                {"AND r.created_at >= ?" if since_ts else ""}
                """,
                [experiment_name, r["variant_id"]] + ([since_ts] if since_ts else []),
            ).fetchall()
            scores = [row[0] for row in v_rows]
            r["win_rate_vs_control"] = (
                round(sum(1 for s in scores if s > control_avg) / len(scores), 3)
                if scores else None
            )
        else:
            r["win_rate_vs_control"] = None
    return result
