from __future__ import annotations

import os
import subprocess
import sys

import pytest


def test_prometheus_scrape_refreshes_sqlite_gauges(tmp_path, monkeypatch):
    pytest.importorskip("prometheus_client")
    monkeypatch.setenv("LUMA_BRAIN_DB", str(tmp_path / "brain.db"))

    from infra.metrics import render_prometheus_metrics
    from utils.luma_brain import brain_connect, create_job, register_or_update_worker

    conn = brain_connect()
    try:
        create_job(conn, job_type="ANALYZE_PATH")
        register_or_update_worker(
            conn,
            worker_name="test-worker",
            worker_type="general",
            status="ONLINE",
        )
    finally:
        conn.close()

    payload, content_type = render_prometheus_metrics()
    text = payload.decode("utf-8")
    assert "text/plain" in content_type
    assert 'livehouse_jobs_status_count{status="QUEUED"} 1.0' in text
    assert 'livehouse_workers_count{kind="total"} 1.0' in text
    assert 'livehouse_workers_count{kind="status:ONLINE"} 1.0' in text


def test_pipeline_document_exports_recorded_otel_spans(tmp_path):
    from utils.pipeline_tracing import PipelineTraceSession

    ended: list[tuple[str, int | None, int | None]] = []

    class FakeSpan:
        def __init__(self, name: str, start_time: int | None) -> None:
            self.name = name
            self.start_time = start_time

        def end(self, end_time=None):
            ended.append((self.name, self.start_time, end_time))

    class FakeTracer:
        def start_span(self, name, *, attributes, start_time=None):
            assert attributes["livehouse.job_trace_id"] == "trace-1"
            return FakeSpan(name, start_time)

    session = PipelineTraceSession(
        job_trace_id="trace-1",
        out_dir=tmp_path,
        settings={
            "debug": False,
            "emit_jsonl": False,
            "otel_enabled": False,
            "otel_tracer_name": "test",
        },
    )
    session._otel = FakeTracer()
    session.append_document(
        {
            "job_trace_id": "trace-1",
            "image_trace_id": "trace-1#img:a.jpg",
            "image": "a.jpg",
            "spans": [
                {
                    "name": "stage3",
                    "start_unix_ms": 1000,
                    "end_unix_ms": 1200,
                    "attributes": {"cache_hit": False},
                }
            ],
        }
    )

    assert ended == [("livehouse.pipeline.stage3", 1_000_000_000, 1_200_000_000)]


def test_prometheus_multiprocess_registry_sums_worker_counters(tmp_path):
    prometheus_client = pytest.importorskip("prometheus_client")
    from prometheus_client import multiprocess

    multiproc_dir = tmp_path / "prometheus"
    multiproc_dir.mkdir()
    env = dict(os.environ)
    env["PROMETHEUS_MULTIPROC_DIR"] = str(multiproc_dir)
    code = (
        "from prometheus_client import Counter; "
        "Counter('livehouse_test_worker_total', 'test').inc({amount})"
    )
    subprocess.run([sys.executable, "-c", code.format(amount=1)], env=env, check=True)
    subprocess.run([sys.executable, "-c", code.format(amount=2)], env=env, check=True)

    registry = prometheus_client.CollectorRegistry()
    multiprocess.MultiProcessCollector(registry, path=str(multiproc_dir))
    text = prometheus_client.generate_latest(registry).decode("utf-8")
    assert "livehouse_test_worker_total 3.0" in text
