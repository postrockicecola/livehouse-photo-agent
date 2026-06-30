"""Aesthetic evaluation pipeline for livehouse photography.

**Compatibility / legacy entry (same stages as production):** use
:class:`~services.processor.pipeline_stage_runner.PipelineStageRunner` via
``tasks.run_job`` for job-driven runs. This module is for config + VLM handle construction,
export helpers, and :meth:`AestheticPipeline.run` (CLI ``run_pipeline.py``, tests, lazy VLM
inside the stage runner).

**Recommended main path:** ingest → seed jobs → ``run_job`` → ``JobExecutor`` →
``PipelineStageRunner`` → inference → artifacts.
"""
import logging
import shutil
import time
from pathlib import Path
from threading import Lock
from typing import Any, Dict

from utils.config_loader import ConfigLoader
from utils.logging_setup import attach_file_handler, configure_logging
from engine.models.vlm_model import LivehouseVLM
from inference.client import inference_client_from_model_config
from services.processor.pipeline_image_ops import apply_delivery_mode_overrides

configure_logging()
attach_file_handler("pipeline.log")
logger = logging.getLogger(__name__)


class AestheticPipeline:
    """
    **Compatibility façade** — not the preferred production entry.

    Production: ``JobExecutor`` → :class:`PipelineStageRunner`. Local/legacy: :meth:`run` delegates
    to the same runner (no partial stage-3 dispatch unless using stage jobs).
    """

    def __init__(
        self,
        config_path: str = None,
        source_dir: str = None,
        trace_id: str | None = None,
        job_id: int | None = None,
        session_id: int | None = None,
        photo_id: int | None = None,
        worker_id: int | None = None,
        pipeline_mode: str | None = None,
    ):
        self._config_path = config_path if config_path is not None else "configs/livehouse.yaml"
        self.config = ConfigLoader.load(self._config_path)

        if source_dir:
            self.config["paths"]["source_dir"] = source_dir

        if pipeline_mode is not None and str(pipeline_mode).strip():
            self.config.setdefault("processing", {})["pipeline_mode"] = str(pipeline_mode).strip()

        apply_delivery_mode_overrides(self.config)

        self.source_dir = Path(self.config["paths"]["source_dir"])
        self.work_dir = Path(self.config["paths"]["work_dir"])

        self.folders = ConfigLoader.get_folder_paths(self.config, self.source_dir)
        self.log_paths = ConfigLoader.get_log_paths(self.config, self.source_dir)

        for folder in self.folders.values():
            folder.mkdir(parents=True, exist_ok=True)

        for log_path in self.log_paths.values():
            log_path.parent.mkdir(parents=True, exist_ok=True)

        model_config = ConfigLoader.get_model_config(self.config)
        max_conc = int(model_config.get("max_concurrent_requests", 4) or 1)
        self._max_inference_queue_size = max(1, int(model_config.get("max_inference_queue_size", 16) or 16))
        self.model_provider = str(model_config.get("provider", "ollama"))
        self.model_name = str(model_config.get("model_name", ""))
        self.trace_id = trace_id
        self.job_id = job_id
        self.session_id = session_id
        self.photo_id = photo_id
        self.worker_id = worker_id
        self.vlm = self._build_inference_client(model_config, max_conc)

        self.file_lock = Lock()
        self.progress_lock = Lock()
        self.stats = {
            "processed": 0,
            "failed": 0,
            "skipped": 0,
            "fast_rejected": 0,
            "stage1_rejected": 0,
            "stage2_rejected": 0,
            "stage1_passed": 0,
            "vlm_fallback": 0,
            "fallback_count": 0,
            "stage3_latencies_sec": [],
            "stage3_fast_pass_latencies_sec": [],
            "stage3_full_pass_latencies_sec": [],
            "stage3_wall_latencies_sec": [],
            "stage3_fast_only_count": 0,
            "stage3_full_count": 0,
            "stage3_early_exit_ratio": 0.0,
        }

        logger.info("Pipeline initialized | Source: %s", self.source_dir)

    def _build_inference_client(self, model_config: Dict[str, Any], max_conc: int) -> Any:
        """
        Build inference entrypoint.
        Default keeps legacy LivehouseVLM; optional switch enables new inference layer.
        """
        provider = str(model_config.get("provider", "ollama") or "ollama").strip().lower()
        use_inference_layer = bool(model_config.get("use_inference_layer", False))
        mq = self._max_inference_queue_size
        _ht = model_config.get("inference_hard_timeout_seconds")
        hard_to: int | None = None if _ht is None or _ht == "" else int(_ht)

        if not use_inference_layer:
            return LivehouseVLM(
                endpoint=model_config["endpoint"],
                model_name=model_config["model_name"],
                timeout=model_config["timeout"],
                temperature=model_config["temperature"],
                num_predict=model_config["num_predict"],
                max_retries=model_config["max_retries"],
                retry_delay=model_config["retry_delay"],
                queue_wait_timeout_seconds=float(model_config.get("queue_wait_timeout_seconds", 60)),
                fallback_model_name=model_config.get("fallback_model_name") or None,
                fallback_num_predict=model_config.get("fallback_num_predict"),
                max_concurrent_requests=max(1, max_conc),
                max_inference_queue_size=mq,
                inference_hard_timeout_seconds=hard_to,
                ollama_endpoints=model_config.get("ollama_endpoints"),
                ollama_ports=model_config.get("ollama_ports"),
                ollama_host=model_config.get("ollama_host"),
            )

        logger.info("Using inference layer provider=%s", provider)
        return inference_client_from_model_config(
            model_config,
            max_concurrent_requests=max(1, max_conc),
            max_inference_queue_size=mq,
            inference_hard_timeout_seconds=hard_to,
        )

    def run(self, max_workers: int = None, enable_checkpoint: bool = True) -> Dict[str, Any]:
        from services.processor.pipeline_stage_runner import PipelineStageRunner

        start_time = time.time()
        runner = PipelineStageRunner(
            config_path=self._config_path,
            source_dir=str(self.source_dir),
            trace_id=self.trace_id,
            job_id=self.job_id,
            worker_id=int(self.worker_id) if self.worker_id is not None else 0,
            session_id=self.session_id,
        )
        runner.run_prepare_input()
        r1 = runner.run_stage1_filter(max_workers=max_workers, enable_checkpoint=enable_checkpoint)
        r2 = runner.run_stage2_fast_score(max_workers=max_workers)
        r3 = runner.run_stage3_vlm(max_workers=max_workers, conn=None)
        wa = runner.run_write_artifact()
        elapsed = time.time() - start_time
        st = wa.get("stats") or {}
        self.stats["processed"] = int(st.get("processed", 0))
        self.stats["failed"] = int(r3.get("failed", 0))
        self.stats["skipped"] = int(r1.get("checkpoint_skipped", 0))
        self.stats["fast_rejected"] = int(st.get("fast_rejected", 0))
        self.stats["vlm_fallback"] = int(st.get("vlm_fallback", 0))
        self.stats["fallback_count"] = int(st.get("fallback_count", 0))
        total = int(r1.get("total", 0))
        stage1_image_count = int(r1.get("stage1_pass", 0)) + int(r1.get("stage1_reject", 0))
        stage2_image_count = int(r2.get("total_in", 0))
        stage3_image_count = int(r3.get("total_in", 0))
        return {
            "total": total,
            "processed": self.stats["processed"],
            "failed": self.stats["failed"],
            "time_cost": round(elapsed, 3),
            "stage1_image_count": stage1_image_count,
            "stage2_image_count": stage2_image_count,
            "stage3_image_count": stage3_image_count,
            "artifact_paths": wa.get("artifact_paths")
            or {
                "analysis_results": None,
                "preview_html": None,
                "folder_galleries": [],
                "launch_scripts": [],
            },
        }

    def export_selected_images(self, category, image_names, export_folder=None):
        """Export selected images to a folder (used by gallery integrations)."""
        from datetime import datetime

        if category not in self.folders:
            return {"success": False, "error": f"Invalid category: {category}"}

        if not image_names:
            return {"success": False, "error": "No images specified"}

        if export_folder is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            export_folder = self.source_dir / "exported_images" / f"export_{timestamp}"
        else:
            export_folder = Path(export_folder)

        export_folder.mkdir(parents=True, exist_ok=True)

        source_dir = self.folders[category]
        success_count = 0
        errors = []

        for image_name in image_names:
            try:
                source_path = source_dir / image_name

                if source_path.exists() and source_path.is_file():
                    dest_path = export_folder / image_name
                    shutil.copy2(source_path, dest_path)
                    success_count += 1
                    logger.info("   ✅ 已导出: %s", image_name)
                else:
                    error_msg = f"{image_name}: 文件不存在"
                    errors.append(error_msg)
                    logger.warning("   ⚠️ %s", error_msg)
            except Exception as e:
                error_msg = f"{image_name}: {str(e)}"
                errors.append(error_msg)
                logger.error("   ❌ %s", error_msg)

        return {
            "success": success_count > 0,
            "count": success_count,
            "path": str(export_folder),
            "errors": errors if errors else None,
        }


if __name__ == "__main__":
    pipeline = AestheticPipeline(config_path="configs/livehouse.yaml")
    pipeline.run()
