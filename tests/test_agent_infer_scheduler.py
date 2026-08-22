"""P0 AgentInferScheduler: priority, reject-when-full, queue-wait expire."""
from __future__ import annotations

import threading
import time

import pytest

from services.agent.infer_scheduler import (
    AgentInferExpired,
    AgentInferRejected,
    AgentInferScheduler,
    wrap_chat_fn,
)


def test_interactive_dequeues_before_batch() -> None:
    sched = AgentInferScheduler(max_queue_size=4, num_workers=1, queue_wait_timeout=5.0)
    started = threading.Event()
    release = threading.Event()
    order: list[str] = []

    def blocker(_messages: list) -> str:
        started.set()
        release.wait(timeout=5.0)
        return "block"

    def tagged(name: str):
        def _fn(_messages: list) -> str:
            order.append(name)
            return name

        return _fn

    try:
        t_block = threading.Thread(target=lambda: sched.submit(blocker, [], kind="batch"))
        t_block.start()
        assert started.wait(timeout=2.0)

        got: dict[str, str] = {}

        def _run(name: str, kind: str) -> None:
            got[name] = sched.submit(tagged(name), [], kind=kind)

        t_batch = threading.Thread(target=_run, args=("batch", "batch"))
        t_int = threading.Thread(target=_run, args=("interactive", "interactive"))
        t_batch.start()
        time.sleep(0.05)
        t_int.start()
        time.sleep(0.05)
        release.set()
        t_batch.join(timeout=3.0)
        t_int.join(timeout=3.0)
        t_block.join(timeout=3.0)
        assert got["interactive"] == "interactive"
        assert got["batch"] == "batch"
        assert order == ["interactive", "batch"]
        snap = sched.snapshot()
        assert snap["completed"] >= 3
        assert snap["rejected"] == 0
        assert snap["expired"] == 0
    finally:
        release.set()
        sched.close()


def test_reject_when_queue_full() -> None:
    sched = AgentInferScheduler(max_queue_size=1, num_workers=1, queue_wait_timeout=5.0)
    started = threading.Event()
    release = threading.Event()

    def blocker(_messages: list) -> str:
        started.set()
        release.wait(timeout=5.0)
        return "block"

    try:
        t = threading.Thread(target=lambda: sched.submit(blocker, [], kind="batch"))
        t.start()
        assert started.wait(timeout=2.0)
        with pytest.raises(AgentInferRejected):
            sched.submit(lambda _m: "nope", [], kind="interactive")
        assert sched.snapshot()["rejected"] == 1
    finally:
        release.set()
        t.join(timeout=3.0)
        sched.close()


def test_expire_when_queue_wait_exceeded() -> None:
    sched = AgentInferScheduler(max_queue_size=2, num_workers=1, queue_wait_timeout=0.08)
    started = threading.Event()
    release = threading.Event()

    def blocker(_messages: list) -> str:
        started.set()
        release.wait(timeout=5.0)
        return "block"

    try:
        t = threading.Thread(target=lambda: sched.submit(blocker, [], kind="batch"))
        t.start()
        assert started.wait(timeout=2.0)
        with pytest.raises(AgentInferExpired):
            sched.submit(lambda _m: "late", [], kind="batch")
        assert sched.snapshot()["expired"] == 1
    finally:
        release.set()
        t.join(timeout=3.0)
        sched.close()


def test_wrap_chat_fn_passthrough() -> None:
    sched = AgentInferScheduler(max_queue_size=2, num_workers=1, queue_wait_timeout=2.0)
    try:
        fn = sched.wrap_chat_fn(lambda messages: f"ok:{messages[0]['content']}")
        assert fn([{"role": "user", "content": "hi"}]) == "ok:hi"
        assert sched.snapshot()["last_wait_ms"] >= 0
    finally:
        sched.close()


def test_wrap_chat_fn_can_disable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIVEHOUSE_AGENT_INFER_SCHEDULER", "0")
    inner = lambda messages: "direct"  # noqa: E731
    assert wrap_chat_fn(inner) is inner
