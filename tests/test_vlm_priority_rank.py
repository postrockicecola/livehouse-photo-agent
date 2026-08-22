"""VLM queue priority: Rank 1 (best Stage2) must dequeue first (min-heap)."""
from __future__ import annotations

import queue

from services.scheduler.priority_queue import vlm_priority_for_rank


def test_best_rank_is_served_before_worst():
    n = 30
    best = vlm_priority_for_rank(rank_one_based=1, batch_size=n)
    worst = vlm_priority_for_rank(rank_one_based=n, batch_size=n)
    assert best < worst

    pq: queue.PriorityQueue[tuple[int, int, str]] = queue.PriorityQueue()
    pq.put((worst, 0, "worst"))
    pq.put((best, 1, "best"))
    first = pq.get()[2]
    assert first == "best"
