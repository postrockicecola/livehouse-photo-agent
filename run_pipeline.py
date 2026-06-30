"""CLI entry: run the Livehouse aesthetic selection pipeline and optional Web gallery.

**Path label: compatibility / legacy CLI** (bypasses ``jobs`` SSOT and Infra timelines).

Same stages as production via :class:`~services.processor.aesthetic_pipeline.AestheticPipeline`
→ :class:`~services.processor.pipeline_stage_runner.PipelineStageRunner`.

**Recommended main path:** ingest → ``tasks.process_brain_ingested`` → seed jobs →
``tasks.run_job`` → ``JobExecutor`` → ``PipelineStageRunner`` → inference → artifacts.

**When to use this file:** local debugging, tests, Go ingest **mode A** ``pipeline-cmd`` subprocess.
"""
from __future__ import annotations

import argparse
import logging
import os
import socket
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Mapping, TypedDict, cast
from urllib.error import URLError
from urllib.request import urlopen

from services.processor.aesthetic_pipeline import AestheticPipeline
from utils.config_loader import ConfigLoader
from utils.gallery_serve import start_gallery_server_background
from utils.infra_listen_probe import livehouse_openapi_probe
from utils.logging_setup import configure_logging
from utils.next_dev_serve import start_next_dev_background
from utils.metrics_exporter import export_metrics_json, export_prometheus_metrics_placeholder

logger = logging.getLogger(__name__)


class PipelineMetrics(TypedDict, total=False):
    total: int
    processed: int
    failed: int
    stage1_image_count: int
    stage2_image_count: int
    stage3_image_count: int
    time_cost: float
    error_count: int
    error_stage: str
    stage_durations: Dict[str, float]
    stage_status: Dict[str, Literal["success", "failed", "skipped"]]
    throughput: float
    success_rate: float


class PipelineMeta(TypedDict):
    source_dir: str
    folders: Dict[str, str]


class PipelineResult(TypedDict):
    meta: PipelineMeta
    metrics: PipelineMetrics


class ServerStartResult(TypedDict, total=False):
    started: bool
    skipped: bool
    ready: bool
    pid: int
    reason: str
    log_file: str
    source_dir: str
    port: int
    status: Literal[
        "ready",
        "failed",
        "skipped",
        "started_not_ready",
        "disabled",
        "reused",
        "reused_foreign",
    ]
    urls: Dict[str, str]
    retries: int
    startup_time: float
    health_check_passed: bool


class PipelineError(Exception):
    """Base pipeline error type for infra-friendly handling."""


class ConfigError(PipelineError):
    """Raised when config validation fails."""


class StageExecutionError(PipelineError):
    """Raised when a stage cannot complete."""

    def __init__(self, stage: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.stage = stage
        self.retryable = retryable


class InfraError(PipelineError):
    """Raised when infra operations (e.g. server lifecycle) fail."""


@dataclass(frozen=True)
class RunOptions:
    config_path: str
    source_dir_override: str | None
    max_workers: int | None
    no_checkpoint: bool
    pipeline_mode: str | None = None


@dataclass(frozen=True)
class BuildPipelineInput:
    options: RunOptions
    source_dir: Path


@dataclass(frozen=True)
class ExecutePipelineInput:
    pipeline: AestheticPipeline
    max_workers: int
    enable_checkpoint: bool


@dataclass
class StageContext:
    cfg: Dict[str, Any]
    options: RunOptions
    source_dir: Path
    max_workers: int
    enable_checkpoint: bool
    pipeline: AestheticPipeline | None = None
    raw_metrics: Mapping[str, Any] | None = None
    metrics: PipelineMetrics | None = None
    folders_cfg: Dict[str, str] | None = None


@dataclass(frozen=True)
class StageSpec:
    name: str
    handler: Callable[[StageContext], None]
    retries: int = 0
    timeout: float | None = None
    optional: bool = False


def _configure_logging() -> None:
    configure_logging()


def _resolve_max_workers(args_workers: int | None, cfg_workers: int | None) -> int:
    cpu_workers = os.cpu_count() or 1
    preferred = args_workers if args_workers is not None else cfg_workers
    if preferred is None or preferred <= 0:
        return cpu_workers
    return preferred


def build_file_summary(source_dir: Path, folders: Mapping[str, str]) -> Dict[str, str]:
    summary: Dict[str, str] = {"preview": str(source_dir / "preview.html")}
    for key, folder_name in folders.items():
        gallery_html = source_dir / folder_name / "gallery.html"
        label = {"best": "Best", "keep": "Keep", "trash": "Trash"}.get(key, key)
        summary[label] = str(gallery_html)
    return summary


def format_server_status(result: ServerStartResult, port: int, folders: Mapping[str, str]) -> Dict[str, str]:
    base = f"http://127.0.0.1:{port}"
    status_info: Dict[str, str] = {"base_url": base}
    labels = {"best": "Best", "keep": "Keep", "trash": "Trash"}
    for key in ("best", "keep", "trash"):
        sub = folders.get(key, key)
        status_info[labels.get(key, key)] = f"{base}/{sub}/gallery.html"
    st = result.get("status")
    if st == "disabled":
        status_info["message"] = "未启动画廊服务（已使用 --no-serve）。"
    elif result.get("skipped") and not result.get("ready"):
        status_info["message"] = (
            f"端口 {port} 已占用，但未识别为 Livehouse Gallery API（OpenAPI 探测失败）；"
            "请关闭占用进程或改用 --serve-port，否则 Lab 导出可能失败。"
        )
    elif result.get("skipped"):
        status_info["message"] = f"端口 {port} 已占用：已探测到 Livehouse Gallery OpenAPI，复用该服务。"
    elif not result.get("started"):
        status_info["message"] = f"服务启动失败: {result.get('reason', 'unknown')}"
    elif result.get("ready"):
        status_info["message"] = f"服务已启动，PID={result.get('pid', '')}"
    else:
        status_info["message"] = f"进程已启动，端口未就绪，PID={result.get('pid', '')}"
    if result.get("log_file"):
        status_info["log_file"] = str(result["log_file"])
    return status_info


def _extract_int_metric(metrics: Mapping[str, Any], *keys: str) -> int:
    for key in keys:
        value = metrics.get(key)
        if isinstance(value, (int, float)):
            return int(value)
    return 0


def _resolve_source_dir(cfg: Mapping[str, Any], source_dir_override: str | None) -> Path:
    if source_dir_override:
        source_dir_raw = source_dir_override
    else:
        paths_cfg = cfg.get("paths")
        if not isinstance(paths_cfg, Mapping):
            raise ConfigError("配置缺失 `paths` 字段，无法确定 source_dir。")
        source_dir_raw = paths_cfg.get("source_dir")
    if not isinstance(source_dir_raw, str) or not source_dir_raw.strip():
        raise ConfigError("配置缺失 `paths.source_dir`，请在配置中设置或传入 --source-dir。")
    source_dir = Path(source_dir_raw).expanduser().resolve()
    if not source_dir.exists():
        raise ConfigError(f"source_dir 不存在: {source_dir}")
    if not source_dir.is_dir():
        raise ConfigError(f"source_dir 不是目录: {source_dir}")
    return source_dir


def _resolve_folders_cfg(pipeline: AestheticPipeline) -> Dict[str, str]:
    folders_raw = pipeline.config.get("paths", {}).get("folders", {})
    if not isinstance(folders_raw, dict):
        logger.warning(
            "invalid folders config",
            extra={"event": "invalid_folders_config", "stage": "orchestration", "status": "skipped"},
        )
        return {}
    return {str(k): str(v) for k, v in folders_raw.items()}


def build_pipeline(payload: BuildPipelineInput) -> AestheticPipeline:
    """Tool-level boundary: create concrete pipeline instance."""
    return AestheticPipeline(
        config_path=payload.options.config_path,
        source_dir=str(payload.source_dir),
        pipeline_mode=payload.options.pipeline_mode,
    )


def execute_pipeline(payload: ExecutePipelineInput) -> PipelineMetrics:
    """Tool-level boundary: execute black-box pipeline and normalize metrics."""
    started_at = time.perf_counter()
    raw_metrics = payload.pipeline.run(
        max_workers=payload.max_workers,
        enable_checkpoint=payload.enable_checkpoint,
    )
    elapsed = time.perf_counter() - started_at
    metrics: PipelineMetrics = {
        "total": _extract_int_metric(raw_metrics, "total", "total_files", "total_images"),
        "processed": _extract_int_metric(raw_metrics, "processed", "processed_files", "processed_images", "success"),
        "failed": _extract_int_metric(raw_metrics, "failed", "failed_files", "errors"),
        "time_cost": elapsed,
        "stage1_image_count": _extract_int_metric(raw_metrics, "stage1_image_count"),
        "stage2_image_count": _extract_int_metric(raw_metrics, "stage2_image_count"),
        "stage3_image_count": _extract_int_metric(raw_metrics, "stage3_image_count"),
    }
    return metrics


def compute_derived_metrics(metrics: PipelineMetrics) -> None:
    """Compute derived metrics in one place to keep orchestration lean."""
    total = metrics.get("total", 0)
    processed = metrics.get("processed", 0)
    time_cost = metrics.get("time_cost", 0.0)
    metrics["throughput"] = float(processed / time_cost) if time_cost > 0 else 0.0
    metrics["success_rate"] = float(processed / total) if total > 0 else 0.0


def pipeline_stages() -> List[StageSpec]:
    """Return stage specs for scheduling/execution orchestration."""
    return [
        StageSpec(name="load", handler=_stage_load, retries=0, timeout=20.0, optional=False),
        StageSpec(name="analyze", handler=_stage_analyze, retries=1, timeout=None, optional=False),
        StageSpec(name="score", handler=_stage_score, retries=0, timeout=10.0, optional=False),
        StageSpec(name="render", handler=_stage_render, retries=0, timeout=10.0, optional=True),
    ]


def _execute_stage_once(ctx: StageContext, stage: StageSpec) -> float:
    """Execute stage handler once and return duration."""
    started_at = time.perf_counter()
    stage.handler(ctx)
    duration = time.perf_counter() - started_at
    if stage.timeout is not None and duration > stage.timeout:
        raise StageExecutionError(
            stage.name,
            f"stage `{stage.name}` timeout after {duration:.3f}s > {stage.timeout:.3f}s",
            retryable=True,
        )
    return duration


def _run_stage(stage: StageSpec, ctx: StageContext) -> None:
    attempt = 0
    if ctx.metrics is None:
        ctx.metrics = PipelineMetrics()
    stage_status = ctx.metrics.setdefault("stage_status", {})
    stage_durations = ctx.metrics.setdefault("stage_durations", {})

    while True:
        started_at = time.perf_counter()
        try:
            logger.info(
                "stage start",
                extra={"event": "stage_start", "stage": stage.name, "attempt": attempt + 1, "status": "running"},
            )
            duration = _execute_stage_once(ctx, stage)
            stage_durations[stage.name] = duration
            stage_status[stage.name] = "success"
            logger.info(
                "stage done",
                extra={"event": "stage_done", "stage": stage.name, "duration": duration, "status": "success"},
            )
            return
        except StageExecutionError as exc:
            duration = time.perf_counter() - started_at
            stage_durations[stage.name] = duration
            ctx.metrics["error_count"] = int(ctx.metrics.get("error_count", 0)) + 1
            ctx.metrics["error_stage"] = stage.name
            logger.exception(
                "stage error",
                extra={
                    "event": "stage_error",
                    "error_type": type(exc).__name__,
                    "stage": stage.name,
                    "attempt": attempt + 1,
                    "duration": duration,
                    "status": "failed",
                    "retryable": exc.retryable,
                },
            )
            if stage.optional:
                stage_status[stage.name] = "skipped"
                logger.warning(
                    "stage optional skipped",
                    extra={"event": "stage_optional_skipped", "stage": stage.name, "status": "skipped"},
                )
                return
            if (not exc.retryable) or attempt >= stage.retries:
                stage_status[stage.name] = "failed"
                raise
            attempt += 1
        except Exception as exc:
            duration = time.perf_counter() - started_at
            stage_durations[stage.name] = duration
            ctx.metrics["error_count"] = int(ctx.metrics.get("error_count", 0)) + 1
            ctx.metrics["error_stage"] = stage.name
            logger.exception(
                "stage error",
                extra={
                    "event": "stage_error",
                    "error_type": type(exc).__name__,
                    "stage": stage.name,
                    "attempt": attempt + 1,
                    "duration": duration,
                    "status": "failed",
                    "retryable": True,
                },
            )
            if stage.optional:
                stage_status[stage.name] = "skipped"
                return
            if attempt >= stage.retries:
                stage_status[stage.name] = "failed"
                raise StageExecutionError(stage.name, f"stage `{stage.name}` failed", retryable=False) from exc
            attempt += 1


def _stage_load(ctx: StageContext) -> None:
    ctx.pipeline = build_pipeline(BuildPipelineInput(options=ctx.options, source_dir=ctx.source_dir))


def _stage_analyze(ctx: StageContext) -> None:
    if ctx.pipeline is None:
        raise StageExecutionError("analyze", "pipeline not initialized", retryable=False)
    ctx.metrics = execute_pipeline(
        ExecutePipelineInput(
            pipeline=ctx.pipeline,
            max_workers=ctx.max_workers,
            enable_checkpoint=ctx.enable_checkpoint,
        )
    )


def _stage_score(ctx: StageContext) -> None:
    if ctx.metrics is None:
        raise StageExecutionError("score", "metrics missing before score stage", retryable=False)
    ctx.metrics.setdefault("error_count", 0)


def _stage_render(ctx: StageContext) -> None:
    if ctx.pipeline is None:
        raise StageExecutionError("render", "pipeline missing before render stage", retryable=False)
    ctx.folders_cfg = _resolve_folders_cfg(ctx.pipeline)


def run_pipeline(cfg: Dict[str, Any], options: RunOptions) -> PipelineResult:
    pipeline_wall_started_at = time.perf_counter()
    processing_cfg = cfg.get("processing", {})
    cfg_workers = processing_cfg.get("max_workers") if isinstance(processing_cfg, dict) else None
    max_workers = _resolve_max_workers(options.max_workers, cast(int | None, cfg_workers))
    enable_checkpoint = bool(processing_cfg.get("enable_checkpoint", True)) if isinstance(processing_cfg, dict) else True
    if options.no_checkpoint:
        enable_checkpoint = False
    source_dir_cfg = _resolve_source_dir(cfg, options.source_dir_override)

    logger.info(
        "pipeline init",
        extra={
            "event": "pipeline_init",
            "status": "starting",
            "stage": "orchestration",
            "config": options.config_path,
            "source_dir": str(source_dir_cfg),
            "workers": max_workers,
        },
    )

    ctx = StageContext(
        cfg=cfg,
        options=options,
        source_dir=source_dir_cfg,
        max_workers=max_workers,
        enable_checkpoint=enable_checkpoint,
        metrics=PipelineMetrics(),
    )
    for stage in pipeline_stages():
        _run_stage(stage, ctx)

    if ctx.pipeline is None or ctx.metrics is None:
        raise StageExecutionError("orchestration", "pipeline execution incomplete", retryable=False)
    if ctx.folders_cfg is None:
        ctx.folders_cfg = {}

    compute_derived_metrics(ctx.metrics)
    total = ctx.metrics.get("total", 0)
    processed = ctx.metrics.get("processed", 0)
    failed = ctx.metrics.get("failed", 0)
    time_cost = ctx.metrics.get("time_cost", 0.0)
    stage1_n = int(ctx.metrics.get("stage1_image_count", 0))
    stage2_n = int(ctx.metrics.get("stage2_image_count", 0))
    stage3_n = int(ctx.metrics.get("stage3_image_count", 0))
    pipeline_wall_s = time.perf_counter() - pipeline_wall_started_at

    logger.info(
        "流水线总耗时 %.2fs（含 load/score/render 编排）；一共 %d 张，Stage1 %d 张，Stage2 %d 张，Stage3 %d 张",
        pipeline_wall_s,
        total,
        stage1_n,
        stage2_n,
        stage3_n,
    )
    logger.info(
        "pipeline completed",
        extra={
            "event": "pipeline_completed",
            "stage": "orchestration",
            "status": "success",
            "duration": time_cost,
            "total": total,
            "processed": processed,
            "failed": failed,
            "error_count": ctx.metrics.get("error_count", 0),
            "error_stage": ctx.metrics.get("error_stage", ""),
            "pipeline_wall_seconds": pipeline_wall_s,
            "stage1_image_count": stage1_n,
            "stage2_image_count": stage2_n,
            "stage3_image_count": stage3_n,
        },
    )
    return {
        "meta": {
            "source_dir": str(ctx.pipeline.source_dir),
            "folders": ctx.folders_cfg,
        },
        "metrics": ctx.metrics,
    }


def _is_port_open(host: str, port: int, timeout_s: float = 0.6) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout_s)
        return sock.connect_ex((host, port)) == 0


def _wait_for_server(host: str, port: int, timeout_s: float) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if _is_port_open(host, port):
            return True
        time.sleep(0.2)
    return False


def _check_health_endpoint(port: int, timeout_s: float = 0.8) -> bool:
    """Best-effort HTTP health probing; try `/` first, then `/health`."""
    try:
        with urlopen(f"http://127.0.0.1:{port}/", timeout=timeout_s) as resp:
            if 200 <= int(resp.status) < 400:
                return True
    except (URLError, TimeoutError, ValueError):
        pass
    try:
        with urlopen(f"http://127.0.0.1:{port}/health", timeout=timeout_s) as resp:
            return 200 <= int(resp.status) < 400
    except (URLError, TimeoutError, ValueError):
        return False


def _try_start_server_once(
    source_dir: Path,
    port: int,
    base_urls: Dict[str, str],
    attempt: int,
    start_total: float,
) -> ServerStartResult:
    """Try to start server once and return a structured snapshot."""
    serve_result = cast(ServerStartResult, start_gallery_server_background(source_dir, port=port))
    port_ready = _wait_for_server("127.0.0.1", port, timeout_s=4.0)
    health_ok = _check_health_endpoint(port) if port_ready else False
    ready = port_ready and health_ok
    serve_result["ready"] = ready
    serve_result["health_check_passed"] = health_ok
    serve_result["source_dir"] = str(source_dir)
    serve_result["port"] = port
    serve_result["urls"] = base_urls
    serve_result["retries"] = attempt
    serve_result["startup_time"] = time.perf_counter() - start_total
    status: Literal[
        "ready",
        "failed",
        "skipped",
        "started_not_ready",
        "disabled",
        "reused",
        "reused_foreign",
    ] = (
        "skipped"
        if serve_result.get("skipped")
        else "ready"
        if serve_result.get("started") and ready
        else "started_not_ready"
        if serve_result.get("started")
        else "failed"
    )
    serve_result["status"] = status
    return serve_result


def start_server(
    enable: bool,
    source_dir: Path,
    port: int,
    folders_cfg: Dict[str, str],
) -> ServerStartResult:
    """Tool-level boundary: start gallery service with health checks and retries."""
    base_urls = {
        key: f"http://127.0.0.1:{port}/{folders_cfg.get(key, key)}/gallery.html"
        for key in ("best", "keep", "trash")
    }
    if not enable:
        return {
            "started": False,
            "skipped": True,
            "ready": False,
            "source_dir": str(source_dir),
            "port": port,
            "status": "disabled",
            "urls": base_urls,
            "reason": "disabled_by_flag",
            "retries": 0,
            "startup_time": 0.0,
            "health_check_passed": False,
        }
    if _is_port_open("127.0.0.1", port):
        probe_ok = livehouse_openapi_probe(port)
        return {
            "started": False,
            "skipped": True,
            "ready": probe_ok,
            "source_dir": str(source_dir),
            "port": port,
            "status": "reused" if probe_ok else "reused_foreign",
            "urls": base_urls,
            "reason": "port_already_in_use_livehouse_ok" if probe_ok else "port_open_not_livehouse_gallery",
            "retries": 0,
            "startup_time": 0.0,
            "health_check_passed": probe_ok,
        }

    retries = 2
    last_result: ServerStartResult = {}
    start_total = time.perf_counter()
    for attempt in range(retries + 1):
        if attempt > 0:
            time.sleep(0.5 * (2 ** (attempt - 1)))
        serve_result = _try_start_server_once(source_dir, port, base_urls, attempt, start_total)
        status = cast(str, serve_result.get("status", "failed"))
        logger.info(
            "server start attempt",
            extra={
                "event": "server_start",
                "stage": "infra",
                "status": status,
                "attempt": attempt,
                "duration": serve_result["startup_time"],
                "port": port,
                "pid": serve_result.get("pid", ""),
                "reason": serve_result.get("reason", ""),
                "ready": serve_result.get("ready", False),
                "health_check_passed": serve_result.get("health_check_passed", False),
            },
        )
        last_result = serve_result
        if serve_result.get("started") and serve_result.get("ready"):
            return serve_result
    raise InfraError(
        f"server failed on port={port} after retries={retries}, "
        f"last_attempt={last_result.get('retries', retries)}, reason={last_result.get('reason', 'unknown')}"
    )


def main() -> None:
    _configure_logging()
    parser = argparse.ArgumentParser(
        description="Livehouse 摄影选片管道：写入 runtime/latest_session.json，并可自动拉起 gallery_server（8080）与 web/next dev（3000）",
    )
    parser.add_argument("--config", default="configs/livehouse.yaml", help="YAML 配置路径（默认 configs/livehouse.yaml）")
    parser.add_argument("--source-dir", default=None, help="覆盖配置中的 paths.source_dir")
    parser.add_argument("--max-workers", type=int, default=None, help="线程数（支持 0/负数/None 自动按 CPU 核数）")
    parser.add_argument(
        "--mode",
        choices=("fast", "balanced", "strict", "delivery"),
        default=None,
        help="Stage3 门控预设：fast（更少 VLM）| balanced | strict（更高召回）| "
        "delivery（交付快览：严控 Stage3 数量、FAST+少量 FULL、静默日志）",
    )
    parser.add_argument("--no-checkpoint", action="store_true", help="不做断点续跑，全部重算")
    parser.add_argument(
        "--no-serve",
        action="store_true",
        help="仅生成静态页与 JSON，不自动启动 gallery_server；Lab 内「导出」将无法使用该 API（除非你另行手动启动）",
    )
    parser.add_argument("--serve-port", type=int, default=8080, help="双排流服务端口（默认 8080，需与 gallery_server 一致）")
    parser.add_argument(
        "--no-next-dev",
        action="store_true",
        help="不自动在后台启动 web/ 的 Next.js 开发服务（默认会在流水线结束后尝试启动）",
    )
    parser.add_argument(
        "--next-dev-port",
        type=int,
        default=3000,
        help="Next.js dev 监听端口（默认 3000，对应 PORT 环境变量）",
    )
    parser.add_argument(
        "--strict-infra",
        action="store_true",
        help="要求 Gallery API 与 Next dev 在对应端口上可用（含「占用但探测为本仓库服务」）；否则以非零退出码失败，避免误以为一键就绪",
    )
    args = parser.parse_args()

    cfg = ConfigLoader.load(args.config)
    run_options = RunOptions(
        config_path=args.config,
        source_dir_override=args.source_dir,
        max_workers=args.max_workers,
        no_checkpoint=args.no_checkpoint,
        pipeline_mode=args.mode,
    )
    run_result = run_pipeline(cfg, run_options)
    meta = run_result["meta"]
    source_dir = Path(meta["source_dir"])
    folders_cfg = meta["folders"]

    file_summary = build_file_summary(source_dir, folders_cfg)
    serve_result = start_server(
        enable=not args.no_serve,
        source_dir=source_dir,
        port=args.serve_port,
        folders_cfg=folders_cfg,
    )
    if serve_result.get("status") == "reused_foreign":
        logger.warning(
            "Gallery 端口 %s 已被占用，且未识别为本仓库 gallery_server（OpenAPI 标题探测失败）。",
            args.serve_port,
        )
    server_status = format_server_status(serve_result, args.serve_port, folders_cfg)

    next_dev_result: Dict[str, Any] = {}
    gallery_origin_for_next: str | None = None
    if not args.no_serve:
        gallery_origin_for_next = f"http://127.0.0.1:{args.serve_port}"

    if not args.no_next_dev:
        next_dev_result = dict(
            start_next_dev_background(
                port=args.next_dev_port,
                gallery_api_origin=gallery_origin_for_next,
            )
        )
        logger.info(
            "next dev",
            extra={
                "event": "next_dev_result",
                "stage": "infra",
                "status": next_dev_result.get("status", ""),
                "port": args.next_dev_port,
                "pid": next_dev_result.get("pid"),
                "skipped": next_dev_result.get("skipped", False),
                "ready": next_dev_result.get("ready", False),
                "reason": next_dev_result.get("reason", ""),
                "command": next_dev_result.get("command", ""),
            },
        )

    if args.strict_infra:
        if not args.no_serve and not serve_result.get("ready"):
            logger.error(
                "strict-infra: 需要可用的 Livehouse Gallery API（端口 %s）。"
                " 当前未就绪：请释放端口、改用 --serve-port，或去掉 --strict-infra。",
                args.serve_port,
            )
            sys.exit(6)
        if not args.no_next_dev and not next_dev_result.get("ready"):
            logger.error(
                "strict-infra: 需要可用的 Next dev（端口 %s）。"
                " 当前未就绪：请释放端口、改用 --next-dev-port，或去掉 --strict-infra。",
                args.next_dev_port,
            )
            sys.exit(7)

    if not args.no_next_dev and next_dev_result.get("skipped") and not next_dev_result.get("ready"):
        logger.warning(
            "Next 端口 %s 已被占用且未识别为 Next.js（响应头探测失败）；"
            "浏览器打开的可能是其它站点，请换端口或结束占用进程。",
            args.next_dev_port,
        )

    metrics_path = source_dir / "metrics.json"
    metrics_payload = {
        "pipeline": {
            "meta": run_result["meta"],
            "metrics": {
                "total": run_result["metrics"].get("total", 0),
                "processed": run_result["metrics"].get("processed", 0),
                "failed": run_result["metrics"].get("failed", 0),
                "time_cost": run_result["metrics"].get("time_cost", 0.0),
                "throughput": run_result["metrics"].get("throughput", 0.0),
                "success_rate": run_result["metrics"].get("success_rate", 0.0),
                "stage1_image_count": run_result["metrics"].get("stage1_image_count", 0),
                "stage2_image_count": run_result["metrics"].get("stage2_image_count", 0),
                "stage3_image_count": run_result["metrics"].get("stage3_image_count", 0),
                "error_count": run_result["metrics"].get("error_count", 0),
                "error_stage": run_result["metrics"].get("error_stage", ""),
            },
        },
        "stages": {
            "status": run_result["metrics"].get("stage_status", {}),
            "durations": run_result["metrics"].get("stage_durations", {}),
        },
        "infra": {
            "server": serve_result,
            "server_status": server_status,
            "next_dev": next_dev_result,
            "file_summary": file_summary,
        },
    }
    export_metrics_json(metrics_payload, metrics_path)
    export_prometheus_metrics_placeholder(source_dir / "metrics.prom", run_result["metrics"])

    logger.info("file summary built", extra={"event": "file_summary", "stage": "infra", "status": "ready"})
    logger.info("server result", extra={"event": "server_result", "stage": "infra", "status": serve_result.get("status", "")})
    logger.info("server status built", extra={"event": "server_status", "stage": "infra", "status": "ready"})
    logger.info("metrics exported", extra={"event": "metrics_export", "stage": "infra", "status": "written", "path": str(metrics_path)})

    if not args.no_next_dev:
        logger.info(
            "Lab (Next dev): http://127.0.0.1:%s/ — logs: web/next_dev.log",
            args.next_dev_port,
        )
    if not args.no_serve:
        logger.info(
            "Gallery API (导出 / 画廊 JSON): http://127.0.0.1:%s/ — logs: %s",
            args.serve_port,
            source_dir / "gallery_server.log",
        )
    if (
        not args.no_next_dev
        and not args.no_serve
        and serve_result.get("ready")
        and next_dev_result.get("ready")
    ):
        logger.info(
            "单次命令已含流水线 + 后台服务：在浏览器打开 Lab 即可浏览与导出（Next 已注入 GALLERY_API_ORIGIN=%s）。",
            gallery_origin_for_next,
        )
    elif not args.no_next_dev and not args.no_serve:
        logger.warning(
            "画廊或 Next 未同时达到可用状态（见上文 server / next dev 日志与 metrics.json 中的 infra 字段）；"
            "导出或 Lab 可能异常，直至端口与进程正确。",
        )


if __name__ == "__main__":
    try:
        main()
    except ConfigError as exc:
        logger.error("config error", extra={"event": "config_error", "error_type": type(exc).__name__, "stage": "orchestration", "status": "failed"})
        sys.exit(2)
    except StageExecutionError as exc:
        logger.error(
            "stage execution error",
            extra={"event": "stage_execution_error", "error_type": type(exc).__name__, "stage": exc.stage, "status": "failed"},
        )
        sys.exit(3)
    except InfraError as exc:
        logger.error("infra error", extra={"event": "infra_error", "error_type": type(exc).__name__, "stage": "infra", "status": "failed"})
        sys.exit(4)
    except PipelineError as exc:
        logger.error("pipeline error", extra={"event": "pipeline_error", "error_type": type(exc).__name__, "stage": "orchestration", "status": "failed"})
        sys.exit(1)
    except KeyboardInterrupt:
        logger.error("interrupted", extra={"event": "interrupted", "stage": "orchestration", "status": "cancelled"})
        sys.exit(130)
