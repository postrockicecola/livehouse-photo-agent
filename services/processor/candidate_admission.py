"""Deterministic, budgeted Stage3 admission; selection never changes image verdicts."""
from __future__ import annotations

from collections import Counter, defaultdict, deque
import hashlib
import math
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from engine.operators.stage2_prefilter import row_ambiguous_tags


def _debug(row: Mapping[str, Any]) -> Mapping[str, Any]:
    return row.get("debug_info") or {}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (ValueError, TypeError):
        return default


def _order(row: Mapping[str, Any]) -> tuple:
    name = str(row["file_name"])
    match = re.fullmatch(r"(.*?)(\d+)", Path(name).stem)
    sequence = (match[1], int(match[2])) if match else (name, -1)
    timestamp = _number(_debug(row).get("capture_time"), -1)
    return (timestamp < 0, timestamp, *sequence, name)


def _fingerprint(row: Mapping[str, Any]) -> bytes:
    return hashlib.sha256(str(row["file_name"]).encode()).digest()


def _quality(row: Mapping[str, Any]) -> float:
    return .6 * _number(row.get("tech_score")) + .4 * _number(row.get("fast_score"))


def _phash(row: Mapping[str, Any]) -> int:
    try:
        return int(row.get("phash") or _debug(row).get("phash") or 0)
    except (ValueError, TypeError):
        return 0


def _groups(rows: Sequence[dict], settings: Mapping[str, Any]) -> list[list[int]]:
    groups: list[list[int]] = []
    gap = max(0, _number(settings.get("burst_gap_seconds"), 2.5))
    distance = max(0, int(settings.get("burst_hash_distance", 10)))
    for i, row in enumerate(rows):
        same = False
        if groups:
            group = groups[-1]
            anchor, previous = rows[group[0]], rows[group[-1]]
            now, start, last = (_number(_debug(r).get("capture_time"), -1)
                                for r in (row, anchor, previous))
            if min(now, start, last) >= 0:
                close = 0 <= now - last <= gap and now - start <= 8
            elif max(now, start, last) < 0:
                # Only a conservative fallback: adjacent sequence numbers AND similar content.
                a, b = _order(previous), _order(row)
                close = a[2] == b[2] and 0 < b[3] - a[3] <= 3
            else:
                close = False
            ph, ah = _phash(row), _phash(anchor)
            same = close and len(group) < 20 and ph > 0 and ah > 0 and (ph ^ ah).bit_count() <= distance
        if same:
            groups[-1].append(i)
        else:
            groups.append([i])
    return groups


def _round_robin(buckets: Mapping[Any, list[int]]) -> list[int]:
    # Scramble bucket order deterministically so a small budget does not favor early times.
    keys = sorted(buckets, key=lambda k: hashlib.sha256(repr(k).encode()).digest())
    queues = deque(deque(buckets[k]) for k in keys)
    result = []
    while queues:
        queue = queues.popleft()
        result.append(queue.popleft())
        if queue:
            queues.append(queue)
    return result


def select_candidates(
    eligible_rows: Sequence[dict], *, settings: Mapping[str, Any],
    mode_ratio: float | None = None, mode: str | None = None,
    seconds_per_image: float = 22.0,
) -> tuple[list[dict], list[dict], dict]:
    rows = sorted(eligible_rows, key=_order)
    groups = _groups(rows, settings)
    n = len(rows)
    ratio = max(0.0, min(1.0, _number(mode_ratio if mode_ratio is not None else settings.get("budget_ratio"), .2)))
    # Explicit modes control the ratio; no independent floor or group expansion in that case.
    floor = 0 if mode else max(0, int(settings.get("min_candidates", 48)))
    group_ratio = 0 if mode else max(0.0, _number(settings.get("group_budget_ratio"), .25))
    budget = min(n, max(floor, math.ceil(n * ratio), math.ceil(len(groups) * group_ratio)))
    if settings.get("max_candidates") is not None:
        budget = min(budget, max(0, int(settings["max_candidates"])))

    group_for = {i: g for g, group in enumerate(groups) for i in group}
    quality = sorted(range(n), key=lambda i: (-_quality(rows[i]), str(rows[i]["file_name"])))
    coverage: dict[tuple, list[int]] = defaultdict(list)
    ambiguous: dict[str, list[int]] = defaultdict(list)
    bins = max(1, int(settings.get("coverage_time_bins", 24)))
    timestamps = [_number(_debug(row).get("capture_time"), -1) for row in rows]
    valid_times = [t for t in timestamps if t >= 0]
    first_time = min(valid_times, default=0)
    time_span = max(1, max(valid_times, default=0) - first_time)
    for i in sorted(range(n), key=lambda i: _fingerprint(rows[i])):
        dbg = _debug(rows[i])
        if valid_times and timestamps[i] >= 0:
            position = (timestamps[i] - first_time) / time_span
            time_bin = min(bins - 1, int(position * bins))
        else:
            time_bin = min(bins - 1, i * bins // max(1, n))
        light = min(3, max(0, int(_number(dbg.get("brightness"), 128) // 64)))
        saturation = min(2, max(0, int(_number(dbg.get("saturation"), 100) // 86)))
        aspect = _number(dbg.get("aspect_ratio"), 1)
        framing = 0 if aspect < .9 else 2 if aspect > 1.1 else 1
        coverage[(time_bin, light, saturation, framing)].append(i)
        for tag in sorted(set(row_ambiguous_tags(rows[i]))):
            ambiguous[tag].append(i)
    by_time: dict[int, dict[tuple, list[int]]] = defaultdict(dict)
    for key, indices in coverage.items():
        by_time[key[0]][key[1:]] = indices
    orders = {
        "quality": quality,
        "coverage": _round_robin({t: _round_robin(buckets) for t, buckets in by_time.items()}),
        "ambiguous": _round_robin(ambiguous),
        "exploration": sorted(range(n), key=lambda i: _fingerprint(rows[i])),
    }
    defaults = {"quality": .80, "coverage": .10, "ambiguous": .07, "exploration": .03}
    weights = {k: max(0, _number((settings.get("lane_weights") or {}).get(k), v)) for k, v in defaults.items()}
    if not any(weights.values()):
        weights = defaults
    total = sum(weights.values())
    shares = {k: budget * v / total for k, v in weights.items()}
    quotas = {k: int(v) for k, v in shares.items()}
    for k in sorted(quotas, key=lambda k: (-(shares[k] - quotas[k]), k))[:budget - sum(quotas.values())]:
        quotas[k] += 1
    chosen: dict[int, str] = {}
    used: Counter = Counter()
    cap = max(1, int(settings.get("representatives_per_burst", 2)))

    def admit(order: Sequence[int], limit: int, lane: str, group_cap: int) -> int:
        count = 0
        for i in order:
            if count >= limit:
                break
            if i not in chosen and used[group_for[i]] < group_cap:
                chosen[i] = lane
                used[group_for[i]] += 1
                count += 1
        return count

    for lane in ("quality", "coverage", "ambiguous", "exploration"):
        admit(orders[lane], quotas[lane], lane, cap)
    # Reallocate unused quotas across all groups before relaxing the burst cap.
    admit(orders["coverage"], budget - len(chosen), "fill", cap)
    relaxed = admit(quality, budget - len(chosen), "fill", n)
    kept = [dict(rows[i]) for i in range(n) if i in chosen]
    skipped = [dict(rows[i]) for i in range(n) if i not in chosen]
    return kept, skipped, {
        "gating_source": "coverage_budget", "pipeline_mode": mode,
        "before": n, "after": len(kept), "skipped": len(skipped),
        "candidate_budget": budget, "budget_ratio": ratio,
        "burst_groups": len(groups), "capture_time_available": len(valid_times),
        "lane_quotas": quotas, "lane_admitted": dict(Counter(chosen.values())),
        "burst_cap_relaxed": relaxed, "stage3_inferences_saved": len(skipped),
        "estimated_gpu_seconds_saved": len(skipped) * seconds_per_image,
    }
