"""Fast-first Stage3 integration tests (requires ImageHash / full processor imports)."""
from __future__ import annotations

import json
from concurrent.futures import Future
from pathlib import Path
from threading import Lock

import pytest

pytest.importorskip("imagehash")

from services.processor.stages.deep_analysis import (
    Stage3FastFirstHooks,
    run_stage3_fast_first,
    stage3_strategy_settings,
)
from utils.stage3_dimensions import STAGE3_DIM_KEYS


def _dim_json_text(score: float = 6.0) -> str:
    d = {k: score for k in STAGE3_DIM_KEYS}
    d["tags"] = []
    d["comments"] = {}
    return json.dumps(d)


class MockClientFastOnly:
    def __init__(self, fast_score: float = 50.0) -> None:
        self.fast_score = fast_score

    def infer_fast_future(self, image_path, prompt, **kwargs):
        f: Future[dict] = Future()
        f.set_result(
            {
                "status": "success",
                "text": json.dumps({"score": self.fast_score, "verdict": "ok", "tags": []}),
                "metadata": {"queue_wait_sec": 0.01},
            }
        )
        return f


class MockClientFastThenFull:
    def infer_fast_future(self, image_path, prompt, **kwargs):
        f: Future[dict] = Future()
        f.set_result(
            {
                "status": "success",
                "text": json.dumps({"score": 90, "verdict": "great", "tags": []}),
                "metadata": {"queue_wait_sec": 0.01},
            }
        )
        return f

    def infer_full_future(self, image_path, prompt, **kwargs):
        f: Future[dict] = Future()
        f.set_result(
            {
                "status": "success",
                "text": _dim_json_text(6.0),
                "metadata": {"queue_wait_sec": 0.02},
            }
        )
        return f


def _base_hooks_stats() -> dict:
    return {
        "processed": 0,
        "vlm_fallback": 0,
        "fallback_count": 0,
        "stage3_latencies_sec": [],
        "stage3_fast_pass_latencies_sec": [],
        "stage3_full_pass_latencies_sec": [],
        "stage3_wall_latencies_sec": [],
    }


def test_fast_first_early_exit_records_positive_wall(tmp_path: Path) -> None:
    img = tmp_path / "a.jpg"
    img.write_bytes(b"")
    config = {
        "evaluation": {"technical_weight": 0.3, "ai_weight": 0.7},
        "stage3": {"strategy": "fast_first", "full_analysis_top_k": 5, "fast_num_predict": 220},
        "processing": {},
    }
    s3_cfg = stage3_strategy_settings(config)
    stats = _base_hooks_stats()
    hooks = Stage3FastFirstHooks(
        append_audit_line=lambda _p, _d: None,
        progress_lock=Lock(),
        stats=stats,
        trace_id=None,
        job_id=None,
        session_id=None,
        photo_id=None,
        worker_id=None,
        model_provider="mock",
        model_name="mock",
    )
    tasks3 = [(1, 1, "a.jpg", str(img), 50.0, {"blur_type": None, "phash": 0}, 0, None)]
    run_stage3_fast_first(MockClientFastOnly(50.0), config, tasks3, 2, s3_cfg, hooks)
    assert stats["processed"] == 1
    assert len(stats["stage3_wall_latencies_sec"]) == 1
    wall = stats["stage3_wall_latencies_sec"][0]
    assert wall > 0
    assert stats["stage3_fast_pass_latencies_sec"][0] > 0
    assert stats["stage3_full_pass_latencies_sec"][0] == 0.0
    assert stats["stage3_wall_latencies_sec"][0] == stats["stage3_latencies_sec"][0]


def test_fast_first_full_follow_up_sums_fast_and_full_wall(tmp_path: Path) -> None:
    img = tmp_path / "b.jpg"
    img.write_bytes(b"")
    config = {
        "evaluation": {"technical_weight": 0.3, "ai_weight": 0.7},
        "stage3": {"strategy": "fast_first", "full_analysis_top_k": 5, "fast_num_predict": 220},
        "processing": {},
    }
    s3_cfg = stage3_strategy_settings(config)
    stats = _base_hooks_stats()
    hooks = Stage3FastFirstHooks(
        append_audit_line=lambda _p, _d: None,
        progress_lock=Lock(),
        stats=stats,
        trace_id=None,
        job_id=None,
        session_id=None,
        photo_id=None,
        worker_id=None,
        model_provider="mock",
        model_name="mock",
    )
    tasks3 = [(1, 1, "b.jpg", str(img), 50.0, {"blur_type": None, "phash": 0}, 0, None)]
    run_stage3_fast_first(MockClientFastThenFull(), config, tasks3, 2, s3_cfg, hooks)
    assert stats["processed"] == 1
    fast_s = stats["stage3_fast_pass_latencies_sec"][0]
    full_s = stats["stage3_full_pass_latencies_sec"][0]
    wall = stats["stage3_wall_latencies_sec"][0]
    assert fast_s > 0 and full_s > 0
    assert wall == pytest.approx(fast_s + full_s, rel=0, abs=1e-6)
