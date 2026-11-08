"""CLI forwarding and Stage3 counts without model calls or source writes."""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from services.processor.aesthetic_pipeline import AestheticPipeline


@pytest.mark.parametrize("provider,processed,fallback,cache_hits,expected", [
    ("openai", 324, 0, 8, 316),
    ("openai", 324, 4, 8, 312),
    ("ollama", 324, 0, 8, 0),
    ("openai", 0, 0, 0, 0),
])
def test_summary_counts_only_stage3_work(provider, processed, fallback, cache_hits, expected):
    pipe = AestheticPipeline.__new__(AestheticPipeline)
    pipe._config_path = "configs/livehouse.yaml"
    pipe.config = {"processing": {"score_all_images": True}}
    pipe.source_dir = Path("/unused")
    pipe.trace_id = "test"
    pipe.job_id = pipe.worker_id = pipe.session_id = None
    pipe.stats = {}
    pipe.model_provider = provider
    runner = MagicMock()
    runner.run_stage1_filter.return_value = {"total": 1625, "stage1_pass": 1619, "stage1_reject": 6}
    runner.run_stage2_fast_score.return_value = {"total_in": 1619}
    runner.run_stage3_vlm.return_value = {
        "total_in": 324, "processed": processed, "vlm_fallback": fallback,
        "checkpoint_skipped": 324 - processed,
        "stage3_vlm_cache": {"stage3_vlm_cache_saved_inferences": cache_hits},
    }
    runner.run_write_artifact.return_value = {"stats": {"processed": 1619}}
    with (
        patch("services.processor.pipeline_stage_runner.PipelineStageRunner", return_value=runner) as factory,
        patch("services.processor.aesthetic_pipeline.logger.info") as log,
    ):
        pipe.run(enable_checkpoint=False)
    assert factory.call_args.kwargs["score_all_images"] is True
    summary = log.call_args.args[1]
    assert summary["vlm_cloud_scored"] == expected
    assert summary["vlm_scored"] == processed - fallback
    assert summary["vlm_checkpoint_skipped"] == 324 - processed
    runner.run_stage3_vlm.assert_called_once_with(max_workers=None, conn=None, enable_checkpoint=False)


def test_cli_build_forwards_score_all_images():
    from run_pipeline import BuildPipelineInput, RunOptions, build_pipeline

    options = RunOptions("configs/livehouse.yaml", None, None, True, score_all_images=True)
    with patch("run_pipeline.AestheticPipeline") as factory:
        build_pipeline(BuildPipelineInput(options, Path("/unused")))
    assert factory.call_args.kwargs["score_all_images"] is True


@pytest.mark.parametrize("status,running", [("QUEUED", False), ("INFERENCING", True)])
def test_gallery_distinguishes_waiting_jobs_from_executing_jobs(status, running):
    from api.gallery_routes import _gallery_analysis_state

    conn = MagicMock()
    conn.execute.return_value.fetchone.side_effect = [{"status": status}, None]
    with (
        patch("api.gallery_routes.brain_connect", return_value=conn),
        patch("utils.studio_sessions.find_brain_session_id", return_value=28),
        patch("utils.studio_sessions.find_runnable_analyze_job_id", return_value=170),
    ):
        assert _gallery_analysis_state("/unused") == (running, 170, status)
    conn.close.assert_called_once()
