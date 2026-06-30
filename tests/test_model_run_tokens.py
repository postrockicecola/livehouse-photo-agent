"""Token/cost accounting: model_runs persistence + load_test harness (no Ollama/GPU)."""
from __future__ import annotations

from dataclasses import asdict


def test_model_run_token_persistence_and_cost_summary(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMA_BRAIN_DB", str(tmp_path / "brain.db"))
    import utils.luma_brain as lb

    conn = lb.brain_connect()
    try:
        job_id = lb.create_job(conn, job_type="ANALYZE_PHOTO")
        rid = lb.create_model_run_and_mark_started(
            conn, job_id=job_id, provider="ollama", model_name="llava:7b"
        )
        # total_tokens omitted on purpose -> derived from prompt + completion.
        lb.mark_model_run_succeeded(
            conn,
            run_id=rid,
            latency_ms=1200,
            final_model="llava:7b",
            prompt_tokens=600,
            completion_tokens=180,
        )

        row = conn.execute(
            "SELECT prompt_tokens, completion_tokens, total_tokens FROM model_runs WHERE id = ?",
            (rid,),
        ).fetchone()
        assert row["prompt_tokens"] == 600
        assert row["completion_tokens"] == 180
        assert row["total_tokens"] == 780

        summary = lb.summarize_model_run_costs(
            conn, input_usd_per_mtok=0.5, output_usd_per_mtok=1.5
        )[0]
        assert summary["runs"] == 1
        assert summary["total_tokens"] == 780
        # 600/1e6*0.5 + 180/1e6*1.5 = 0.00057 -> per 1k inferences = 0.57
        assert abs(summary["est_cost_per_1k_usd"] - 0.57) < 1e-6

        by_model = lb.summarize_model_run_costs(conn, group_by_model=True)
        assert by_model[0]["final_model"] == "llava:7b"
        assert by_model[0]["completion_tokens"] == 180
    finally:
        conn.close()


def test_mark_succeeded_without_tokens_keeps_null(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMA_BRAIN_DB", str(tmp_path / "brain.db"))
    import utils.luma_brain as lb

    conn = lb.brain_connect()
    try:
        job_id = lb.create_job(conn, job_type="ANALYZE_PHOTO")
        rid = lb.create_model_run_and_mark_started(conn, job_id=job_id, provider="mock")
        lb.mark_model_run_succeeded(conn, run_id=rid, latency_ms=10)
        row = conn.execute(
            "SELECT prompt_tokens, total_tokens FROM model_runs WHERE id = ?", (rid,)
        ).fetchone()
        assert row["prompt_tokens"] is None
        assert row["total_tokens"] is None
        # Unpriced summary still reports counts/latency without crashing.
        summary = lb.summarize_model_run_costs(conn)[0]
        assert summary["runs"] == 1
        assert summary["runs_with_token_usage"] == 0
        assert summary["est_cost_per_1k_usd"] == 0.0
    finally:
        conn.close()


def test_load_test_simulate_scenario_and_renderers():
    from scripts.load_test import (
        CostModel,
        SimulatedTokenProvider,
        render_markdown,
        render_svg,
        run_scenario,
    )

    provider = SimulatedTokenProvider(
        min_latency_ms=2,
        max_latency_ms=4,
        prompt_tokens=120,
        completion_tokens=20,
        seed=7,
    )
    res = run_scenario(
        concurrency=2,
        requests=6,
        provider=provider,
        model_name="sim",
        image_path="/tmp/loadtest.jpg",
        prompt="rate it",
        queue_wait_timeout_seconds=600.0,
        timeout=30,
        cost_model=CostModel(gpu_hourly_usd=1.0),
    )
    assert res.success == 6
    assert res.errors == 0
    assert res.throughput_rps > 0
    assert res.completion_tokens == 120  # 6 * 20
    assert res.decode_tokens_per_sec > 0
    assert res.est_cost_per_1k_usd > 0
    assert res.cost_basis == "gpu_hourly"
    assert res.latency_p99_ms >= res.latency_p50_ms

    doc = {
        "mode": "simulate",
        "generated_at": "now",
        "config": {"model": "sim", "endpoint": None, "requests": 6, "num_predict": 256},
        "scenarios": [asdict(res)],
    }
    assert render_svg(doc).startswith("<svg")
    assert "concurrency" in render_markdown(doc)


def test_load_test_token_pricing_basis():
    from scripts.load_test import CostModel, SimulatedTokenProvider, run_scenario

    provider = SimulatedTokenProvider(
        min_latency_ms=1, max_latency_ms=2, prompt_tokens=1000, completion_tokens=100, seed=1
    )
    res = run_scenario(
        concurrency=1,
        requests=4,
        provider=provider,
        model_name="sim",
        image_path="/tmp/x.jpg",
        prompt="p",
        queue_wait_timeout_seconds=600.0,
        timeout=30,
        cost_model=CostModel(input_usd_per_mtok=1.0, output_usd_per_mtok=2.0),
    )
    # per request: 1000/1e6*1 + 100/1e6*2 = 0.0012 -> per 1k = 1.2
    assert res.cost_basis == "token_pricing"
    assert abs(res.est_cost_per_1k_usd - 1.2) < 1e-6
