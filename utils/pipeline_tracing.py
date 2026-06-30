"""
Per-image pipeline tracing: spans, metrics, and JSONL timeline export for Gantt / flamegraphs.

Optional OpenTelemetry: if ``opentelemetry.trace`` is importable and enabled in config, emits spans
(no exporter wiring here — use env + auto-instrumentation or an OTLP exporter in process).
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping

logger = logging.getLogger(__name__)

_TRACE_SCHEMA = "livehouse.pipeline.trace.v1"


def _slug(s: str, max_len: int = 120) -> str:
    t = re.sub(r"[^a-zA-Z0-9._-]+", "_", (s or "").strip())[:max_len].strip("_")
    return t or "trace"


def make_image_trace_id(job_trace_id: str, file_name: str) -> str:
    """Stable per-image id within a job/session trace (basename is unique in one folder)."""
    j = (job_trace_id or "").strip() or "unknown"
    fn = (file_name or "").strip() or "image"
    return f"{j}#img:{fn}"


def pipeline_tracing_settings(config: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = (config or {}).get("observability") or {}
    if not isinstance(raw, dict):
        raw = {}
    pt = raw.get("pipeline_tracing") or {}
    if not isinstance(pt, dict):
        pt = {}
    env_on = os.environ.get("LIVEHOUSE_PIPELINE_TRACE", "").strip().lower() in ("1", "true", "yes", "on")
    debug_env = os.environ.get("LIVEHOUSE_PIPELINE_TRACE_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")
    debug_img = (os.environ.get("LIVEHOUSE_PIPELINE_TRACE_IMAGE") or "").strip()
    otel_raw = raw.get("opentelemetry") or {}
    otel = otel_raw if isinstance(otel_raw, dict) else {}
    return {
        "enabled": bool(pt.get("enabled")) or env_on,
        "emit_jsonl": bool(pt.get("emit_jsonl", True)),
        "debug": bool(pt.get("debug")) or debug_env,
        "debug_image": str(pt.get("debug_image") or debug_img or "").strip(),
        "otel_enabled": bool(otel.get("enabled")),
        "otel_tracer_name": str(otel.get("tracer_name") or "livehouse.pipeline"),
    }


def _otel_tracer(name: str) -> Any:
    try:
        from opentelemetry import trace as otel_trace  # type: ignore[import-not-found]

        return otel_trace.get_tracer(name)
    except Exception:
        return None


@dataclass
class SpanEvent:
    name: str
    start_unix_ms: int
    end_unix_ms: int
    start_mono: float
    end_mono: float
    attributes: dict[str, Any] = field(default_factory=dict)


class ImagePipelineRecorder:
    """Thread-safe span buffer for one image; produces one JSON document per flush."""

    def __init__(
        self,
        *,
        job_trace_id: str,
        image_trace_id: str,
        file_name: str,
        debug: bool,
    ) -> None:
        self.job_trace_id = job_trace_id
        self.image_trace_id = image_trace_id
        self.file_name = file_name
        self.debug = debug
        self._lock = threading.Lock()
        self._spans: list[SpanEvent] = []
        self._routing: list[dict[str, Any]] = []
        self._attrs: dict[str, Any] = {}

    def set_attrs(self, **kwargs: Any) -> None:
        with self._lock:
            self._attrs.update({k: v for k, v in kwargs.items() if v is not None})

    def record_routing(self, payload: dict[str, Any]) -> None:
        if not payload:
            return
        with self._lock:
            self._routing.append(dict(payload))

    def add_span(
        self,
        name: str,
        *,
        start_mono: float,
        end_mono: float,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        su = int(time.time() * 1000)
        du_ms = max(0, int((end_mono - start_mono) * 1000))
        ev = SpanEvent(
            name=name,
            start_unix_ms=su - du_ms,
            end_unix_ms=su,
            start_mono=start_mono,
            end_mono=end_mono,
            attributes=dict(attributes or {}),
        )
        with self._lock:
            self._spans.append(ev)

    @contextmanager
    def span(self, name: str, **attrs: Any) -> Iterator[None]:
        t0 = time.perf_counter()
        u0 = int(time.time() * 1000)
        try:
            yield
        finally:
            t1 = time.perf_counter()
            u1 = int(time.time() * 1000)
            with self._lock:
                self._spans.append(
                    SpanEvent(
                        name=name,
                        start_unix_ms=u0,
                        end_unix_ms=max(u1, u0),
                        start_mono=t0,
                        end_mono=t1,
                        attributes=dict(attrs),
                    )
                )

    def add_inference_subspans(
        self,
        *,
        parent_start_mono: float,
        parent_end_mono: float,
        queue_wait_sec: float,
        model_infer_sec: float,
        postprocess_sec: float,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Child timeline under stage3 wall clock (best-effort Gantt segments)."""
        total = max(parent_end_mono - parent_start_mono, 1e-9)
        qw = max(0.0, float(queue_wait_sec))
        mi = max(0.0, float(model_infer_sec))
        po = max(0.0, float(postprocess_sec))
        scale = (qw + mi + po) / total if total > 0 else 1.0
        if scale <= 0:
            scale = 1.0
        t = parent_start_mono
        ext = dict(extra or {})

        def seg(name: str, sec: float, attr_key: str) -> None:
            nonlocal t
            if sec <= 0:
                return
            frac = (sec / scale) / max(qw + mi + po, 1e-9) * total if (qw + mi + po) > 0 else sec
            t1 = min(parent_end_mono, t + min(frac, total))
            self.add_span(
                name,
                start_mono=t,
                end_mono=t1,
                attributes={attr_key: round(sec, 4), **ext},
            )
            t = t1

        seg("queue_wait", qw, "queue_wait_sec")
        seg("inference", mi, "model_infer_sec")
        seg("postprocess", po, "postprocess_sec")

    def to_document(self) -> dict[str, Any]:
        with self._lock:
            spans = list(self._spans)
            routing = list(self._routing)
            attrs = dict(self._attrs)
        mono0 = min((s.start_mono for s in spans), default=time.perf_counter())
        timeline = []
        for s in spans:
            timeline.append(
                {
                    "name": s.name,
                    "start_mono": round(s.start_mono - mono0, 6),
                    "end_mono": round(s.end_mono - mono0, 6),
                    "duration_ms": max(0, int((s.end_mono - s.start_mono) * 1000)),
                    "start_unix_ms": s.start_unix_ms,
                    "end_unix_ms": s.end_unix_ms,
                    "attributes": dict(s.attributes),
                }
            )
        out: dict[str, Any] = {
            "schema": _TRACE_SCHEMA,
            "job_trace_id": self.job_trace_id,
            "image_trace_id": self.image_trace_id,
            "image": self.file_name,
            "captured_at_unix_ms": int(time.time() * 1000),
            "attributes": attrs,
            "spans": timeline,
        }
        if routing and self.debug:
            out["routing"] = routing[:48]
        return out


class PipelineTraceSession:
    """One session per pipeline job: JSONL under staged dir, optional OTEL."""

    def __init__(
        self,
        *,
        job_trace_id: str,
        out_dir: Path,
        settings: dict[str, Any],
    ) -> None:
        self.job_trace_id = job_trace_id
        self.out_dir = out_dir
        self.settings = settings
        self.debug = bool(settings.get("debug"))
        self.debug_image = str(settings.get("debug_image") or "")
        self.emit_jsonl = bool(settings.get("emit_jsonl", True))
        self._otel = _otel_tracer(str(settings.get("otel_tracer_name"))) if settings.get("otel_enabled") else None
        self._write_lock = threading.Lock()
        self._jsonl_path: Path | None = None
        if self.emit_jsonl:
            self.out_dir.mkdir(parents=True, exist_ok=True)
            self._jsonl_path = self.out_dir / f"pipeline_traces_{_slug(job_trace_id)}.jsonl"

    def should_trace_image(self, file_name: str) -> bool:
        if not self.debug_image:
            return True
        return file_name.strip() == self.debug_image or file_name.strip().endswith(self.debug_image)

    def recorder(self, file_name: str) -> ImagePipelineRecorder | None:
        if not self.should_trace_image(file_name):
            return None
        itid = make_image_trace_id(self.job_trace_id, file_name)
        return ImagePipelineRecorder(
            job_trace_id=self.job_trace_id,
            image_trace_id=itid,
            file_name=file_name,
            debug=self.debug,
        )

    def append_document(self, doc: dict[str, Any]) -> None:
        if not self.emit_jsonl or self._jsonl_path is None:
            return
        line = json.dumps(doc, ensure_ascii=False)
        with self._write_lock:
            with open(self._jsonl_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        if self.debug:
            logger.info("pipeline_trace %s", line[:500])

    def maybe_otel_span(self, name: str, **attrs: Any) -> Any:
        if self._otel is None:
            return _NoopCm()
        return _OtelSpanCm(self._otel, name, attrs)


@dataclass
class _NoopCm:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *args: Any) -> None:
        return None


class _OtelSpanCm:
    def __init__(self, tracer: Any, name: str, attrs: dict[str, Any]) -> None:
        self._tracer = tracer
        self._name = name
        self._attrs = attrs
        self._span = None

    def __enter__(self) -> None:
        try:
            self._span = self._tracer.start_span(self._name)
            for k, v in self._attrs.items():
                if v is not None:
                    self._span.set_attribute(str(k), str(v) if not isinstance(v, (bool, int, float)) else v)
        except Exception:
            self._span = None
        return None

    def __exit__(self, *args: Any) -> None:
        try:
            if self._span is not None:
                self._span.end()
        except Exception:
            pass
        return None


def trace_out_dir(source_dir: Path) -> Path:
    return source_dir / ".luma_pipeline_staged" / "pipeline_traces"


def build_trace_session(
    config: Mapping[str, Any] | None,
    *,
    job_trace_id: str,
    source_dir: Path,
) -> PipelineTraceSession | None:
    st = pipeline_tracing_settings(config)
    if not st["enabled"]:
        return None
    out = trace_out_dir(source_dir)
    return PipelineTraceSession(job_trace_id=job_trace_id, out_dir=out, settings=st)


def merge_inference_trace_attrs(
    inference_extra: dict[str, Any] | None,
    *,
    image_trace_id: str,
    job_trace_id: str,
    file_name: str,
) -> dict[str, Any]:
    """Copy-on-write merge for inference client metadata (queue propagates to workers)."""
    md = dict(inference_extra or {})
    md["image_trace_id"] = image_trace_id
    md["job_trace_id"] = job_trace_id
    md["pipeline_image"] = file_name
    return md


def extract_inference_metrics_from_response(response: Mapping[str, Any] | None) -> dict[str, Any]:
    """Normalize queue / retry / fallback / cache fields from inference payload."""
    if not isinstance(response, Mapping):
        return {}
    meta = response.get("metadata") or {}
    if not isinstance(meta, Mapping):
        meta = {}
    led = meta.get("inference_ledger") or {}
    if not isinstance(led, Mapping):
        led = {}
    attempts = led.get("attempts") or []
    n_attempts = len(attempts) if isinstance(attempts, list) else 0
    retry_count = max(0, n_attempts - 1) if n_attempts else 0
    fb = bool(led.get("router_fallback_used") or meta.get("degraded"))
    ch = meta.get("outcome") == "cache_hit" or (
        isinstance(meta.get("cache_hit"), dict) and len(meta.get("cache_hit") or {}) > 0
    )
    qw_ms = int(float(meta.get("queue_wait_sec") or 0) * 1000)
    pipe = meta.get("pipeline_inference") if isinstance(meta.get("pipeline_inference"), dict) else {}
    return {
        "queue_wait_ms": qw_ms,
        "retry_count": int(pipe.get("http_retry_count", retry_count)),
        "router_fallback": fb,
        "cache_hit": bool(ch),
        "inference_status": str(response.get("status") or ""),
        "vlm_attempts": n_attempts,
    }


def emit_stage3_partial_trace(
    session: PipelineTraceSession | None,
    file_name: str,
    *,
    segment: str,
    wall_start_mono: float,
    wall_end_mono: float,
    stage3_result: Mapping[str, Any] | None = None,
    raw_infer_response: Mapping[str, Any] | None = None,
) -> None:
    """Emit one JSONL trace row for Stage3 (fast-first, cache, or full-only paths)."""
    if session is None:
        return
    rec = session.recorder(file_name)
    if rec is None:
        return
    rec.add_span(
        "stage3",
        start_mono=wall_start_mono,
        end_mono=wall_end_mono,
        attributes={"segment_detail": segment},
    )
    sm = (stage3_result or {}).get("stage3_meta") or {}
    lb = sm.get("latency_breakdown") if isinstance(sm.get("latency_breakdown"), dict) else {}
    qw = float(lb.get("queue_wait_sec") or 0.0)
    mi = float(lb.get("model_infer_sec") or 0.0)
    po = float(lb.get("postprocess_sec") or 0.0)
    if qw + mi + po > 1e-6:
        rec.add_inference_subspans(
            parent_start_mono=wall_start_mono,
            parent_end_mono=wall_end_mono,
            queue_wait_sec=qw,
            model_infer_sec=mi,
            postprocess_sec=po,
            extra={"segment": segment},
        )
    inf = extract_inference_metrics_from_response(raw_infer_response) if raw_infer_response else {}
    if not inf and stage3_result is not None:
        outcome = str((stage3_result.get("stage3_meta") or {}).get("outcome") or "")
        inf = {
            "cache_hit": outcome == "cache_hit",
            "router_fallback": bool(stage3_result.get("inference_degraded")),
            "inference_status": "error" if stage3_result.get("error") else "success",
        }
    flush_image_trace(session, rec, inference_summary=inf, segment=segment)


def flush_image_trace(
    session: PipelineTraceSession | None,
    recorder: ImagePipelineRecorder | None,
    *,
    inference_summary: Mapping[str, Any] | None = None,
    segment: str | None = None,
) -> None:
    if session is None or recorder is None:
        return
    if inference_summary:
        recorder.set_attrs(**dict(inference_summary))
    doc = recorder.to_document()
    if segment:
        doc["segment"] = segment
    session.append_document(doc)
