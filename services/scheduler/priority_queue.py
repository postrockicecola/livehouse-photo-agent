"""
Priority queue for Stage3 image inference: higher Stage2 (fast) score is served first.

Uses :mod:`heapq` (min-heap on ``(-score, tie_breaker)``) so :meth:`InferencePriorityQueue.dequeue`
returns the highest-scoring task next.

Example:

    from pathlib import Path
    from services.scheduler.priority_queue import (
        ImageTask,
        InferencePriorityQueue,
        image_tasks_from_stage3_rows,
        log_top_inference_tasks,
        sort_tasks_by_priority,
    )

    rows = [...]  # eligibility dicts from eligible_after_stage2.jsonl
    tasks = [
        ImageTask(path="/data/a.jpg", score=0.91, metadata={"id": 1}),
        ImageTask(path="/data/b.jpg", score=0.72, metadata={"id": 2}),
    ]
    q = InferencePriorityQueue()
    q.enqueue(tasks)
    log_top_inference_tasks(logger, tasks, n=10)
    ordered = sort_tasks_by_priority(tasks)  # highest score first

    work = [(1, rows[0]), (2, rows[1])]
    tasks2 = image_tasks_from_stage3_rows(work, source_dir=Path("/data"))
"""
from __future__ import annotations

import heapq
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ImageTask:
    """One inferencible image with Stage2-derived priority."""

    path: str
    score: float
    metadata: Dict[str, Any] = field(default_factory=dict)


class InferencePriorityQueue:
    """
    Max-score-first priority queue backed by :func:`heapq.heappush` / :func:`heapq.heappop`.

    Internal ordering is ``(-score, seq)`` so larger ``score`` pops first; ``seq`` breaks ties
    stably.
    """

    def __init__(self) -> None:
        self._heap: List[Tuple[float, int, ImageTask]] = []
        self._seq = 0

    def enqueue(self, tasks: Iterable[ImageTask]) -> None:
        for t in tasks:
            self._seq += 1
            heapq.heappush(self._heap, (-float(t.score), self._seq, t))

    def dequeue(self) -> Optional[ImageTask]:
        if not self._heap:
            return None
        _, _, task = heapq.heappop(self._heap)
        return task

    def __len__(self) -> int:
        return len(self._heap)

    def __bool__(self) -> bool:
        return bool(self._heap)

    def peek_top(self, n: int = 10) -> List[ImageTask]:
        """
        Inspect up to ``n`` tasks with highest scores **without** removing them.

        Copies and sorts heap entries; cost is O(N log N).
        """
        if n <= 0 or not self._heap:
            return []
        ranked = sorted(self._heap, key=lambda x: (x[0], x[1]))
        return [x[2] for x in ranked[:n]]

    def drain_desc(self) -> Iterator[ImageTask]:
        """Yield all tasks from highest ``score`` to lowest."""
        while self._heap:
            t = self.dequeue()
            if t is None:
                break
            yield t


def sort_tasks_by_priority(tasks: Iterable[ImageTask]) -> List[ImageTask]:
    """Return tasks sorted by ``score`` descending (stable tie-break by enqueue order)."""
    q = InferencePriorityQueue()
    q.enqueue(tasks)
    return list(q.drain_desc())


def log_top_inference_tasks(
    lg: logging.Logger,
    tasks: Iterable[ImageTask],
    *,
    n: int = 10,
    label: str = "stage3_inference_queue",
) -> None:
    """Log up to ``n`` tasks with highest Stage2 scores before inference."""
    ranked = sort_tasks_by_priority(tasks)
    top = ranked[: max(0, n)]
    lg.info("%s: top %s tasks by Stage2 score (highest first)", label, min(n, len(ranked)))
    for i, t in enumerate(top, start=1):
        name = t.metadata.get("file_name") or Path(t.path).name
        lg.info("  %s. score=%.6f path=%s", i, float(t.score), name)


def image_tasks_from_stage3_rows(
    work_items: List[Tuple[int, Dict[str, Any]]],
    *,
    source_dir: Path,
) -> List[ImageTask]:
    """
    Build :class:`ImageTask` entries from Stage3 ``work_items`` pairs ``(dispatch_id, row)``.

    ``row`` is an eligibility record with ``file_name`` and ``fast_score`` (Stage2).
    """
    out: List[ImageTask] = []
    for dispatch_id, row in work_items:
        fp = str(source_dir / str(row["file_name"]))
        try:
            fs = float(row.get("fast_score") or 0.0)
        except (TypeError, ValueError):
            fs = 0.0
        out.append(
            ImageTask(
                path=fp,
                score=fs,
                metadata={
                    "dispatch_id": int(dispatch_id),
                    "file_name": str(row.get("file_name") or ""),
                    "row": row,
                },
            )
        )
    return out


def ordered_work_items_from_sorted_tasks(sorted_tasks: List[ImageTask]) -> List[Tuple[int, Dict[str, Any]]]:
    """Rebuild ``(dispatch_id, row)`` pairs after priority ordering."""
    ordered: List[Tuple[int, Dict[str, Any]]] = []
    for t in sorted_tasks:
        md = t.metadata
        jid = int(md["dispatch_id"])
        row = md["row"]
        ordered.append((jid, row))
    return ordered


def reorder_stage3_work_by_fast_score(
    work_items: List[Tuple[int, Dict[str, Any]]],
    *,
    source_dir: Path,
) -> Tuple[List[Tuple[int, Dict[str, Any]]], List[ImageTask]]:
    """
    Sort Stage3 work by descending ``fast_score`` (dispatch ids unchanged).

    Returns ``(ordered_work_items, tasks)`` where ``tasks`` matches the order used for logging.
    """
    tasks = image_tasks_from_stage3_rows(work_items, source_dir=source_dir)
    ranked = sort_tasks_by_priority(tasks)
    return ordered_work_items_from_sorted_tasks(ranked), tasks


def vlm_priority_for_rank(*, rank_one_based: int, batch_size: int) -> int:
    """
    Map batch processing rank to VLM ``queue_priority``.

    Rank 1 = highest Stage2 score → largest priority integer so backends that prefer higher
    ``priority`` admit those requests first.
    """
    n = max(0, int(batch_size))
    r = max(1, min(int(rank_one_based), n)) if n else 1
    return max(1, n - r + 1)


__all__ = [
    "ImageTask",
    "InferencePriorityQueue",
    "image_tasks_from_stage3_rows",
    "log_top_inference_tasks",
    "ordered_work_items_from_sorted_tasks",
    "reorder_stage3_work_by_fast_score",
    "sort_tasks_by_priority",
    "vlm_priority_for_rank",
]
