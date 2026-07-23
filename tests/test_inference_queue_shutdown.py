"""Inference queue shutdown rejects queued work and new submits."""
from __future__ import annotations

import time
from concurrent import futures as concurrent_futures

from inference.providers.mock import MockProvider
from inference.queue import PrioritizedInferenceQueue, _InferenceJob
from inference.router import InferenceRouter
from inference.types import InferenceRequest


def test_shutdown_rejects_queued_and_new_submit():
    router = InferenceRouter(
        primary_provider=MockProvider(),
        primary_model_name="mock-vlm",
    )
    q = PrioritizedInferenceQueue(
        router=router,
        num_workers=1,
        max_queue_size=8,
        batch_aggregate_window_ms=0,
    )
    # Stop workers before injecting so they cannot claim the staged job.
    q._stop.set()
    time.sleep(0.05)

    req = InferenceRequest(image_path="/tmp/x.jpg", prompt="p", priority=0, metadata={})
    lane = q._lane_for_request(req)
    fut_queued: concurrent_futures.Future[dict] = concurrent_futures.Future()
    lane.admission.acquire()
    job = _InferenceJob(
        request=req,
        enqueued_mono=time.monotonic(),
        admitted_mono=time.monotonic(),
        client_future=fut_queued,
    )
    lane.pq.put((0, next(lane.seq), job))

    q.shutdown(cancel_queued=True)
    assert fut_queued.done()
    assert fut_queued.result()["status"] == "error"
    assert "shutdown" in fut_queued.result()["error"].lower()

    late = q.submit_future(image_path="/tmp/y.jpg", prompt="p")
    assert late.done()
    assert late.result()["status"] == "error"
    assert "shutting down" in late.result()["error"].lower()
