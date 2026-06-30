"""Stage 3: VLM dimensional scoring for Livehouse photos."""
from __future__ import annotations

import copy
import logging
import random
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Protocol, Tuple, TypeAlias, runtime_checkable

import requests

from inference.parsers import (
    _mirror_bilingual_pair,
    clean_json_response,
    default_fast_stage3_parsed,
    default_stage3_parsed,
    norm_bilingual_text,
    parse_dimensional_response,
    parse_fast_vlm_response,
)
from inference.types import InferenceRequest, inference_status_ok
from infra.metrics import record_stage3_early_exit_counts
from utils.config_loader import ConfigLoader
from engine.operators.stage2_prefilter import hamming_64, phash_dedup_settings
from services.processor.stages.stage3_prompt_builder import (
    STAGE3_PROMPT_VERSION,
    build_stage3_fast_prompt,
    build_stage3_prompt,
)
from services.processor.stages.stage3_output_validation import (
    classify_parse_failure,
    sanitize_stage3_parsed,
)
from services.processor.stages.stage4_editing_runner import (
    maybe_run_stage4_editing,
    stage4_editing_settings,
)
from services.processor.stage3_latency_metrics import cache_hit_latency_triplet, record_stage3_latency_lists
from services.processor.stages.stage3_postprocess import (
    apply_dynamic_weights,
    calibrate_dimension_scores,
    copy_dimensions_for_audit,
    weighted_ai_score,
)
from services.cache.stage3_cache import CACHE_HIT_META_KEY, Stage3PHashCache
from utils.pipeline_tracing import emit_stage3_partial_trace, make_image_trace_id, merge_inference_trace_attrs
from utils.stage3_result import (
    assert_stage3_result_consistent,
    attach_stage3_result,
    empty_dimension_slots_none,
    fast_stage3_result,
    full_stage3_result,
)

logger = logging.getLogger(__name__)

# Bump when prompt shape changes (audit / A-B tests).
STAGE3_PROMPT_PROFILE = STAGE3_PROMPT_VERSION
STAGE3_FAST_PROMPT_PROFILE = "fast-v2-layered"

_RETRYABLE_REQUEST_ERRORS = (
    requests.Timeout,
    requests.ConnectionError,
    requests.exceptions.ReadTimeout,
    requests.exceptions.ChunkedEncodingError,
)


@runtime_checkable
class Stage3PredictClient(Protocol):
    def predict(self, image_path: str, prompt: str, retry_count: int = 0, priority: int = 0) -> Dict[str, Any]:
        ...


@runtime_checkable
class Stage3InferRouter(Protocol):
    def infer(
        self,
        request: InferenceRequest,
        *,
        force_fallback: bool = False,
        fallback_num_predict: int | None = None,
    ) -> Any:
        ...


Stage3InferenceClient: TypeAlias = Stage3PredictClient | Stage3InferRouter


def log_stage3_inference_queue_metrics(
    log: logging.Logger,
    inference_client: Stage3InferenceClient,
    *,
    batch_phase: str,
) -> None:
    """Emit queue saturation metrics when the client exposes ``inference_queue_observability``."""
    obs_fn = getattr(inference_client, "inference_queue_observability", None)
    if not callable(obs_fn):
        return
    try:
        metrics = obs_fn()
    except Exception:
        log.debug("inference_queue_observability failed", exc_info=True)
        return
    log.info(
        "stage3_inference_queue batch_phase=%s metrics=%s",
        batch_phase,
        {
            "queue_size": metrics.get("queue_size"),
            "active_workers": metrics.get("active_workers"),
            "idle_time": metrics.get("idle_time"),
            "max_inflight": metrics.get("max_inflight"),
        },
    )


def _run_inference(
    inference_client: Stage3InferenceClient,
    image_path: str,
    prompt: str,
    queue_priority: int,
    log_context: dict[str, Any] | None = None,
    inference_extra_metadata: dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """
    Minimal adapter:
    - preferred: inference client with predict(...)
    - compatible: router-like object with infer(InferenceRequest)
    """
    ctx = dict(log_context or {})
    md_extra = dict(inference_extra_metadata or {})
    if isinstance(inference_client, Stage3PredictClient):
        try:
            return inference_client.predict(
                image_path,
                prompt,
                priority=queue_priority,
                trace_id=ctx.get("trace_id"),
                job_id=ctx.get("job_id"),
                session_id=ctx.get("session_id"),
                photo_id=ctx.get("photo_id"),
                worker_id=ctx.get("worker_id"),
                provider=ctx.get("provider"),
                model_name=ctx.get("model"),
                inference_extra_metadata=md_extra if md_extra else None,
            )
        except TypeError:
            # Backward compatibility: legacy LivehouseVLM signature.
            return inference_client.predict(image_path, prompt, priority=queue_priority)
    if isinstance(inference_client, Stage3InferRouter):
        req = InferenceRequest(
            image_path=image_path,
            prompt=prompt,
            priority=queue_priority,
            model_name=ctx.get("model"),
            metadata={
                **md_extra,
                "trace_id": ctx.get("trace_id"),
                "job_id": ctx.get("job_id"),
                "session_id": ctx.get("session_id"),
                "photo_id": ctx.get("photo_id"),
                "worker_id": ctx.get("worker_id"),
                "provider": ctx.get("provider"),
            },
        )
        res = inference_client.infer(req)
        if hasattr(res, "to_dict"):
            return res.to_dict()
        if isinstance(res, dict):
            return res
    raise TypeError("Unsupported inference client: expected .predict(...) or .infer(...)")


def _run_inference_fast(
    inference_client: Stage3InferenceClient,
    image_path: str,
    prompt: str,
    queue_priority: int,
    fast_num_predict: int,
    log_context: dict[str, Any] | None = None,
    inference_extra_metadata: dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Fast Stage3: prefer ``infer_fast`` when present, else ``num_predict`` in metadata."""
    ctx = dict(log_context or {})
    md_extra = dict(inference_extra_metadata or {})
    md_extra["num_predict"] = int(md_extra.get("num_predict") or fast_num_predict)
    infer_fast = getattr(inference_client, "infer_fast", None)
    if callable(infer_fast):
        return infer_fast(
            image_path,
            prompt,
            priority=queue_priority,
            fast_num_predict=int(md_extra["num_predict"]),
            trace_id=ctx.get("trace_id"),
            job_id=ctx.get("job_id"),
            session_id=ctx.get("session_id"),
            photo_id=ctx.get("photo_id"),
            worker_id=ctx.get("worker_id"),
            provider=ctx.get("provider"),
            model_name=ctx.get("model"),
            inference_extra_metadata=md_extra if md_extra else None,
        )
    return _run_inference(
        inference_client,
        image_path,
        prompt,
        queue_priority,
        log_context=log_context,
        inference_extra_metadata=md_extra,
    )


def _run_inference_full(
    inference_client: Stage3InferenceClient,
    image_path: str,
    prompt: str,
    queue_priority: int,
    log_context: dict[str, Any] | None = None,
    inference_extra_metadata: dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Full dimensional Stage3: prefer ``infer_full`` when the client exposes it."""
    ctx = dict(log_context or {})
    md_extra = dict(inference_extra_metadata or {})
    infer_full = getattr(inference_client, "infer_full", None)
    if callable(infer_full):
        return infer_full(
            image_path,
            prompt,
            priority=queue_priority,
            trace_id=ctx.get("trace_id"),
            job_id=ctx.get("job_id"),
            session_id=ctx.get("session_id"),
            photo_id=ctx.get("photo_id"),
            worker_id=ctx.get("worker_id"),
            provider=ctx.get("provider"),
            model_name=ctx.get("model"),
            inference_extra_metadata=md_extra if md_extra else None,
        )
    return _run_inference(
        inference_client,
        image_path,
        prompt,
        queue_priority,
        log_context=log_context,
        inference_extra_metadata=md_extra,
    )


_infer_sync_fallback_pool_singleton: ThreadPoolExecutor | None = None


def _infer_sync_fallback_pool() -> ThreadPoolExecutor:
    """Rare fallback when the client lacks infer_*_future (router-only mocks)."""
    global _infer_sync_fallback_pool_singleton
    if _infer_sync_fallback_pool_singleton is None:
        import os as _os

        _infer_sync_fallback_pool_singleton = ThreadPoolExecutor(
            max_workers=max(8, min(64, (_os.cpu_count() or 4) * 8)),
            thread_name_prefix="stage3_infer_sync_fb",
        )
    return _infer_sync_fallback_pool_singleton


def _infer_future_fast(
    inference_client: Stage3InferenceClient,
    image_path: str,
    prompt: str,
    queue_priority: int,
    fast_num_predict: int,
    *,
    log_context: dict[str, Any] | None = None,
    inference_extra_metadata: dict[str, Any] | None = None,
) -> Future[Dict[str, Any]]:
    ctx = dict(log_context or {})
    md_extra = dict(inference_extra_metadata or {})
    md_extra["num_predict"] = int(md_extra.get("num_predict") or fast_num_predict)
    infer_ff = getattr(inference_client, "infer_fast_future", None)
    if callable(infer_ff):
        return infer_ff(
            image_path,
            prompt,
            priority=queue_priority,
            fast_num_predict=int(md_extra["num_predict"]),
            trace_id=ctx.get("trace_id"),
            job_id=ctx.get("job_id"),
            session_id=ctx.get("session_id"),
            photo_id=ctx.get("photo_id"),
            worker_id=ctx.get("worker_id"),
            provider=ctx.get("provider"),
            model_name=ctx.get("model"),
            inference_extra_metadata=md_extra if md_extra else None,
        )
    return _infer_sync_fallback_pool().submit(
        _run_inference_fast,
        inference_client,
        image_path,
        prompt,
        queue_priority,
        int(md_extra["num_predict"]),
        log_context,
        md_extra,
    )


def _infer_future_full(
    inference_client: Stage3InferenceClient,
    image_path: str,
    prompt: str,
    queue_priority: int,
    *,
    log_context: dict[str, Any] | None = None,
    inference_extra_metadata: dict[str, Any] | None = None,
) -> Future[Dict[str, Any]]:
    ctx = dict(log_context or {})
    md_extra = dict(inference_extra_metadata or {})
    infer_fuf = getattr(inference_client, "infer_full_future", None)
    if callable(infer_fuf):
        return infer_fuf(
            image_path,
            prompt,
            priority=queue_priority,
            trace_id=ctx.get("trace_id"),
            job_id=ctx.get("job_id"),
            session_id=ctx.get("session_id"),
            photo_id=ctx.get("photo_id"),
            worker_id=ctx.get("worker_id"),
            provider=ctx.get("provider"),
            model_name=ctx.get("model"),
            inference_extra_metadata=md_extra if md_extra else None,
        )
    return _infer_sync_fallback_pool().submit(
        _run_inference_full,
        inference_client,
        image_path,
        prompt,
        queue_priority,
        log_context,
        md_extra,
    )


def stage3_strategy_settings(config: Dict[str, Any]) -> Dict[str, Any]:
    raw = config.get("stage3") or {}
    return {
        "strategy": str(raw.get("strategy") or "fast_first").strip().lower(),
        "full_analysis_top_k": max(0, int(raw.get("full_analysis_top_k", 5) or 0)),
        "full_analysis_score_threshold": float(raw.get("full_analysis_score_threshold", 85) or 85),
        "fast_num_predict": max(64, int(raw.get("fast_num_predict", 220) or 220)),
    }


def should_run_full_after_fast(
    result_fast: Mapping[str, Any],
    *,
    debug_info: Mapping[str, Any] | None = None,
) -> bool:
    """
    After fast Stage3, decide whether dimensional full inference is worth the cost.

    Uses fast dict shape from :func:`analyze_stage3_fast` (score, tags, optional dimensions).
    ``debug_info`` is reserved for future signals; composition uses ``dimensions`` only.
    """
    _ = debug_info
    score = float(result_fast.get("score") or result_fast.get("fast_ai_score") or 0.0)
    tags = list(result_fast.get("tags") or [])

    if score >= 85:
        return True

    tags_l = [str(t).lower() for t in tags]
    if any("moment" in t for t in tags_l) or any("emotion" in t for t in tags_l):
        return True

    dims = result_fast.get("dimensions") or {}
    comp = float(
        dims.get("composition")
        or dims.get("composition_framing")
        or 0
    )
    if comp >= 8:
        return True

    return False


def pick_full_inference_targets_after_fast(
    fast_by_name: Mapping[str, Mapping[str, Any]],
    task_by_name: Mapping[str, Tuple[Any, ...]],
    ordered_file_names: List[str],
    *,
    max_full: int,
    config: Mapping[str, Any] | None = None,
) -> tuple[set[str], dict[str, int] | None]:
    """
    Among images whose fast pass suggests full VLM (see :func:`should_run_full_after_fast`),
    keep up to ``max_full`` instances, preferring higher fast AI scores first.
    When ``max_full`` is 0, no full dimensional runs are scheduled.

    Before taking Top-K, drops duplicate file names in the score ordering and, when
    ``debug_info["phash"]`` is non-zero, near-duplicates (Hamming ≤ ``max_hamming``
    from config ``phash_near_dup``).

    Returns ``(targets, metrics)`` where ``metrics`` is ``None`` when ``max_full <= 0``.
    Metrics are for this invocation only (not accumulated across Stage3 batches).
    """
    if max_full <= 0:
        return set(), None

    qualifying: List[Tuple[float, str]] = []
    for fn in ordered_file_names:
        fr = fast_by_name.get(fn)
        if fr is None or fr.get("error"):
            continue
        task_t = task_by_name.get(fn)
        if task_t is None:
            continue
        dbg = task_t[5]
        dbg_m = dbg if isinstance(dbg, Mapping) else None
        if not should_run_full_after_fast(fr, debug_info=dbg_m):
            continue
        sc = float(fr.get("fast_ai_score") or fr.get("score") or 0.0)
        qualifying.append((sc, str(fn)))

    qualifying.sort(key=lambda x: (-x[0], x[1]))
    ps = phash_dedup_settings(config or {})
    max_h = int(ps.get("max_hamming", 10) or 10)

    unique_names: List[str] = []
    seen_paths: set[str] = set()
    kept_phashes: list[int] = []
    for _, name in qualifying:
        if name in seen_paths:
            continue
        task_t = task_by_name.get(name)
        ph = 0
        if task_t is not None:
            dbg = task_t[5]
            dbg_m = dbg if isinstance(dbg, Mapping) else None
            if dbg_m is not None:
                ph = int(dbg_m.get("phash", 0) or 0)
        if ph > 0 and kept_phashes:
            if any(hamming_64(ph, kp) <= max_h for kp in kept_phashes):
                continue
        seen_paths.add(name)
        if ph > 0:
            kept_phashes.append(ph)
        unique_names.append(name)

    before_dedup = len(qualifying)
    after_dedup = len(unique_names)
    removed_count = before_dedup - after_dedup
    if removed_count > before_dedup:
        logger.warning(
            "topk_dedup invariant violated: removed_count=%s input_count=%s",
            removed_count,
            before_dedup,
        )
    logger.info(
        "topk_dedup before_dedup=%s after_dedup=%s removed_count=%s",
        before_dedup,
        after_dedup,
        removed_count,
    )
    metrics = {
        "before_dedup": before_dedup,
        "after_dedup": after_dedup,
        "removed_count": removed_count,
    }
    return set(unique_names[:max_full]), metrics


def try_stage3_cache_hit_raw(
    stage3_cache: Stage3PHashCache | None,
    image_phash: int,
    *,
    blur_eff: Optional[str],
    image_path: str,
) -> Dict[str, Any] | None:
    """Return finalized Stage3 dict on cache hit, else ``None``."""
    if stage3_cache is None or int(image_phash) == 0:
        return None
    cached_raw = stage3_cache.get_cached_result(int(image_phash))
    if cached_raw is None:
        return None
    hit_meta = dict(cached_raw.pop(CACHE_HIT_META_KEY, {}))
    logger.info(
        "Stage3 VLM cache %s hit image=%s matched_phash=%s hamming=%s",
        hit_meta.get("kind"),
        Path(image_path).name,
        hit_meta.get("matched_phash"),
        hit_meta.get("hamming"),
    )
    return _finalize_cache_hit_result(cached_raw, blur_eff=blur_eff, hit_meta=hit_meta)


def _blur_effective(blur_type: Optional[str], stage1: Optional[Dict[str, Any]]) -> Optional[str]:
    """Single source for blur profile: argument wins, else Stage1 ``blur_type``."""
    if blur_type is not None:
        return blur_type
    if stage1:
        v = stage1.get("blur_type")
        if v is not None and str(v).strip():
            return str(v).strip()
    return None


def _retry_sleep(attempt: int) -> None:
    """Exponential backoff with jitter (PR2)."""
    base = 0.4 * (2**attempt)
    time.sleep(min(8.0, base) * (0.85 + 0.3 * random.random()))


def _exception_retryable(exc: BaseException) -> bool:
    if isinstance(exc, _RETRYABLE_REQUEST_ERRORS):
        return True
    if isinstance(exc, requests.HTTPError):
        code = getattr(getattr(exc, "response", None), "status_code", None)
        return code in (429, 502, 503, 504)
    return False


def _vlm_response_retryable(response: Dict[str, Any]) -> bool:
    if inference_status_ok(str(response.get("status") or "")):
        return False
    err = (response.get("error") or "").lower()
    needles = (
        "timeout",
        "timed out",
        "connection",
        "reset",
        "503",
        "429",
        "502",
        "524",
        "queue",
        "wait exceeded",
        "temporarily",
        "broken pipe",
        "eof",
    )
    return any(n in err for n in needles)


def _stage3_meta(
    *,
    attempt: int,
    max_retries: int,
    blur_eff: Optional[str],
    latency_ms: float,
    outcome: str,
    extra: Optional[Dict[str, Any]] = None,
    prompt_profile: str | None = None,
) -> Dict[str, Any]:
    m: Dict[str, Any] = {
        "attempt": attempt,
        "max_retries": max_retries,
        "blur_effective": blur_eff,
        "latency_ms": round(latency_ms, 1),
        "prompt_profile": prompt_profile or STAGE3_PROMPT_PROFILE,
        "outcome": outcome,
    }
    if extra:
        m.update(extra)
    return m


def _finalize_cache_hit_result(
    cached: Dict[str, Any],
    *,
    blur_eff: Optional[str],
    hit_meta: Dict[str, Any],
) -> Dict[str, Any]:
    """Normalize metadata for a cache-served Stage3 payload (no VLM call)."""
    out = copy.deepcopy(cached)
    sm = dict(out.get("stage3_meta") or {})
    sm["prompt_profile"] = sm.get("prompt_profile") or STAGE3_PROMPT_PROFILE
    sm["blur_effective"] = blur_eff
    sm["latency_ms"] = 0.1
    sm["outcome"] = "cache_hit"
    sm["cache_hit"] = dict(hit_meta)
    sm["latency_breakdown"] = {
        "queue_wait_sec": 0.0,
        "model_infer_sec": 0.0,
        "postprocess_sec": 0.0,
    }
    out["stage3_meta"] = sm
    out["error"] = False
    return out


def _attach_stage4_editing_after_stage3(
    result: Dict[str, Any],
    *,
    inference_client: Stage3InferenceClient,
    config: Dict[str, Any],
    image_path: str,
    queue_priority: int,
    log_context: dict[str, Any] | None,
    inference_extra_metadata: Dict[str, Any] | None,
    infer_acc: Optional[Dict[str, float]],
    used_fallback_defaults: bool,
    from_cache_hit: bool,
) -> Dict[str, Any]:
    """Run optional Stage4 editing VLM and merge ``editing_suggestions`` into ``result``."""
    if result.get("error"):
        return result
    cfg = stage4_editing_settings(config)
    if not cfg["enabled"]:
        return result
    if from_cache_hit:
        if cfg["skip_on_cache_hit"]:
            return result
        if result.get("editing_suggestions"):
            return result

    editing, adjustments, s4_meta = maybe_run_stage4_editing(
        inference_client=inference_client,
        config=config,
        image_path=image_path,
        stage3_success=result,
        queue_priority=queue_priority,
        log_context=log_context,
        inference_extra_metadata=inference_extra_metadata,
        infer_acc=infer_acc,
        used_fallback_defaults=used_fallback_defaults,
    )
    result["editing_suggestions"] = editing
    if adjustments:
        result["editing_adjustments"] = adjustments
    sm = dict(result.get("stage3_meta") or {})
    sm["stage4_editing"] = s4_meta
    if infer_acc is not None:
        tot_s = float(sm.get("latency_ms") or 0) / 1000.0
        qw = infer_acc.get("queue_wait_sec", 0.0)
        mi = infer_acc.get("model_infer_sec", 0.0)
        sm["latency_breakdown"] = {
            "queue_wait_sec": round(qw, 2),
            "model_infer_sec": round(mi, 2),
            "postprocess_sec": round(max(0.0, tot_s - qw - mi), 2),
        }
    result["stage3_meta"] = sm
    return result


def _timing_slice_from_response(response: Dict[str, Any], wall_sec: float) -> tuple[float, float]:
    """Return (queue_wait_sec, model_infer_sec) for one inference call."""
    meta = response.get("metadata") or {}
    qw = float(meta.get("queue_wait_sec") or 0.0)
    led = meta.get("inference_ledger") or {}
    pm = led.get("provider_latency_ms")
    if pm is not None:
        return qw, float(pm) / 1000.0
    return qw, max(0.0, wall_sec - qw)


def analyze_stage3_fast(
    inference_client: Stage3InferenceClient,
    config: Dict[str, Any],
    image_path: str,
    blur_type: Optional[str] = None,
    retry_count: int = 0,
    max_retries: int = 2,
    queue_priority: int = 0,
    stage1_features: Optional[Dict[str, Any]] = None,
    log_context: dict[str, Any] | None = None,
    inference_extra_metadata: Dict[str, Any] | None = None,
    prefetched_primary_response: Optional[Dict[str, Any]] = None,
    _wall_t0: float | None = None,
    _infer_acc: Optional[Dict[str, float]] = None,
    fast_num_predict: int = 220,
) -> Dict[str, Any]:
    """
    Stage3 fast path: single 0–100 score + one-line verdict + tags.
    Per-dimension rubric slots exist as ``None`` in ``dimensions`` and in the ``stage3_result`` unified payload (fast mode).
    """
    first_entry = _wall_t0 is None
    if first_entry:
        _wall_t0 = time.perf_counter()
        _infer_acc = {"queue_wait_sec": 0.0, "model_infer_sec": 0.0}
    t_wall0 = _wall_t0
    acc = _infer_acc
    assert acc is not None

    blur_eff = _blur_effective(blur_type, stage1_features)

    def _elapsed_ms() -> float:
        return (time.perf_counter() - t_wall0) * 1000.0

    # Initialized before the try block so the closure below can safely reference it.
    response: Dict[str, Any] = {}

    def _latency_breakdown_extra(base: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        out = dict(base or {})
        tot_s = _elapsed_ms() / 1000.0
        qw = acc["queue_wait_sec"]
        mi = acc["model_infer_sec"]
        post_s = max(0.0, tot_s - qw - mi)
        out["latency_breakdown"] = {
            "queue_wait_sec": round(qw, 2),
            "model_infer_sec": round(mi, 2),
            "postprocess_sec": round(post_s, 2),
        }
        # Capture token usage from the provider response metadata so the model_runs
        # ledger can store prompt/completion token counts for cost attribution.
        try:
            rm = dict(response.get("metadata") or {})
            pt = rm.get("prompt_eval_count") or rm.get("prompt_tokens")  # Ollama / vLLM
            ct = rm.get("eval_count") or rm.get("completion_tokens")
            if pt is not None:
                out["prompt_tokens"] = int(pt)
            if ct is not None:
                out["completion_tokens"] = int(ct)
        except Exception:
            pass
        return out

    try:
        prompt_primary = build_stage3_fast_prompt(blur_eff=blur_eff, stage1_features=stage1_features)
        if prefetched_primary_response is not None:
            response = prefetched_primary_response
            iw0 = time.perf_counter()
            iw1 = iw0
        else:
            iw0 = time.perf_counter()
            response = _run_inference_fast(
                inference_client,
                image_path,
                prompt_primary,
                queue_priority,
                int(fast_num_predict),
                log_context=log_context,
                inference_extra_metadata=inference_extra_metadata,
            )
            iw1 = time.perf_counter()
        if inference_status_ok(str(response.get("status") or "")):
            qwi, mis = _timing_slice_from_response(response, iw1 - iw0)
            acc["queue_wait_sec"] += qwi
            acc["model_infer_sec"] += mis

        if not inference_status_ok(str(response.get("status") or "")):
            if retry_count < max_retries and _vlm_response_retryable(response):
                logger.warning(
                    "Fast VLM non-success (retryable) image=%s attempt=%s err=%s",
                    Path(image_path).name,
                    retry_count,
                    response.get("error"),
                )
                _retry_sleep(retry_count)
                return analyze_stage3_fast(
                    inference_client,
                    config,
                    image_path,
                    blur_type,
                    retry_count + 1,
                    max_retries,
                    queue_priority=queue_priority,
                    stage1_features=stage1_features,
                    log_context=log_context,
                    inference_extra_metadata=inference_extra_metadata,
                    _wall_t0=t_wall0,
                    _infer_acc=acc,
                    fast_num_predict=fast_num_predict,
                )
            return {
                "score": 0,
                "fast_ai_score": 0.0,
                "reason": f"API Error: {response.get('error', 'Unknown error')}",
                "verdict": "",
                "tags": [],
                "dimensions": {},
                "dimensions_raw": {},
                "weakness": "",
                "dimension_comments": {},
                "editing_suggestions": [],
                "stage3_postprocess": None,
                "stage3_meta": _stage3_meta(
                    attempt=retry_count,
                    max_retries=max_retries,
                    blur_eff=blur_eff,
                    latency_ms=_elapsed_ms(),
                    outcome="vlm_error",
                    extra=_latency_breakdown_extra({"error_detail": str(response.get("error", ""))[:300]}),
                    prompt_profile=STAGE3_FAST_PROMPT_PROFILE,
                ),
                "error": True,
            }

        raw_content = response.get("text", "").strip()
        clean_json = clean_json_response(raw_content)
        parsed = parse_fast_vlm_response(clean_json, raw_content)
        parse_note: Dict[str, Any] = {"parse_attempts": 1}
        response_r: Dict[str, Any] | None = None

        if not parsed:
            prompt_retry = (
                build_stage3_fast_prompt(blur_eff=blur_eff, stage1_features=stage1_features)
                + '\nEmit only: {"score":<number 0-100>,"verdict":"<one line>","tags":["t1","t2"]}\n'
            )
            retry_md = dict(inference_extra_metadata or {})
            retry_md["num_predict"] = max(int(fast_num_predict), 280)
            iw2 = time.perf_counter()
            response_r = _run_inference_fast(
                inference_client,
                image_path,
                prompt_retry,
                queue_priority,
                int(retry_md["num_predict"]),
                log_context=log_context,
                inference_extra_metadata=retry_md,
            )
            iw3 = time.perf_counter()
            if inference_status_ok(str(response_r.get("status") or "")):
                qwi, mis = _timing_slice_from_response(response_r, iw3 - iw2)
                acc["queue_wait_sec"] += qwi
                acc["model_infer_sec"] += mis
            parse_note["parse_attempts"] = 2
            if inference_status_ok(str(response_r.get("status") or "")):
                raw_r = response_r.get("text", "").strip()
                clean_r = clean_json_response(raw_r)
                parsed = parse_fast_vlm_response(clean_r, raw_r)

        used_fallback_defaults = False
        if not parsed:
            parsed = default_fast_stage3_parsed()
            used_fallback_defaults = True
            parse_note["used_fallback_defaults"] = True

        outcome = "fallback_defaults" if used_fallback_defaults else "success"
        src_resp = response
        if parse_note.get("parse_attempts") == 2 and response_r is not None:
            src_resp = response_r

        degraded_inf = (
            bool(src_resp.get("is_fallback"))
            or str(src_resp.get("status") or "").strip().upper() == "DEGRADED"
            or bool((src_resp.get("metadata") or {}).get("degraded"))
        )
        if degraded_inf and outcome == "success":
            outcome = "degraded_inference"

        verdict_bi = _mirror_bilingual_from_parsed_verdict(parsed.get("verdict"))
        ai_score = float(parsed["score"])
        verdict_line = verdict_bi["zh"] or verdict_bi["en"] or ""

        dims_none = empty_dimension_slots_none()

        success: Dict[str, Any] = {
            "score": ai_score,
            "fast_ai_score": ai_score,
            "reason": verdict_line,
            "reason_bilingual": verdict_bi,
            "verdict": verdict_line,
            "verdict_bilingual": verdict_bi,
            "tags": list(parsed.get("tags") or []),
            "dimensions": dims_none,
            "dimensions_raw": {},
            "weakness": "",
            "dimension_comments": {},
            "editing_suggestions": [],
            "stage3_postprocess": None,
            "stage3_meta": _stage3_meta(
                attempt=retry_count,
                max_retries=max_retries,
                blur_eff=blur_eff,
                latency_ms=_elapsed_ms(),
                outcome=outcome,
                extra=_latency_breakdown_extra(dict(parse_note)),
                prompt_profile=STAGE3_FAST_PROMPT_PROFILE,
            ),
            "error": False,
            "inference_degraded": bool(degraded_inf and outcome == "degraded_inference"),
        }
        attach_stage3_result(
            success,
            fast_stage3_result(
                score=ai_score,
                verdict=verdict_line,
                inference_degraded=bool(success.get("inference_degraded")),
            ),
        )
        assert_stage3_result_consistent(success)
        return success

    except Exception as e:
        if retry_count < max_retries and _exception_retryable(e):
            logger.warning(
                "Fast Stage3 retryable error image=%s attempt=%s: %s",
                Path(image_path).name,
                retry_count,
                e,
            )
            _retry_sleep(retry_count)
            return analyze_stage3_fast(
                inference_client,
                config,
                image_path,
                blur_type,
                retry_count + 1,
                max_retries,
                queue_priority=queue_priority,
                stage1_features=stage1_features,
                log_context=log_context,
                inference_extra_metadata=inference_extra_metadata,
                _wall_t0=t_wall0,
                _infer_acc=acc,
                fast_num_predict=fast_num_predict,
            )

        logger.error("Fast analysis failed after %s retries: %s", max_retries, e)
        return {
            "score": 0,
            "fast_ai_score": 0.0,
            "reason": f"Error: {type(e).__name__}",
            "verdict": "",
            "tags": [],
            "dimensions": {},
            "dimensions_raw": {},
            "weakness": "",
            "dimension_comments": {},
            "editing_suggestions": [],
            "stage3_postprocess": None,
            "stage3_meta": _stage3_meta(
                attempt=retry_count,
                max_retries=max_retries,
                blur_eff=blur_eff,
                latency_ms=_elapsed_ms(),
                outcome="exception",
                extra=_latency_breakdown_extra(
                    {"error_type": type(e).__name__, "error_msg": str(e)[:400]}
                ),
                prompt_profile=STAGE3_FAST_PROMPT_PROFILE,
            ),
            "error": True,
        }


def _mirror_bilingual_from_parsed_verdict(raw: Any) -> Dict[str, str]:
    if isinstance(raw, dict):
        return _mirror_bilingual_pair(raw)
    return _mirror_bilingual_pair(norm_bilingual_text(raw))


def analyze_with_dimensions(
    inference_client: Stage3InferenceClient,
    config: Dict[str, Any],
    image_path: str,
    blur_type: Optional[str] = None,
    retry_count: int = 0,
    max_retries: int = 2,
    queue_priority: int = 0,
    stage1_features: Optional[Dict[str, Any]] = None,
    log_context: dict[str, Any] | None = None,
    inference_extra_metadata: Dict[str, Any] | None = None,
    prefetched_primary_response: Optional[Dict[str, Any]] = None,
    _wall_t0: float | None = None,
    _infer_acc: Optional[Dict[str, float]] = None,
    stage3_cache: Stage3PHashCache | None = None,
    image_phash: int | None = None,
) -> Dict[str, Any]:
    """
    Deep AI analysis with dimensional scoring (Stage 3).

    P0: ``stage1_features`` = Stage1 ``debug_info`` for calibration + dynamic weights.
    PR1: ``blur_effective`` unifies blur; ``stage3_meta`` for audit; calibration caps logged.
    PR2: selective retry + backoff on transport / soft VLM failures / parse miss.
    PR3: shorter Tier-A prompt + STAGE3_DIM_PROMPT_LINES + compact Stage1 line.
    """
    first_entry = _wall_t0 is None
    if first_entry:
        _wall_t0 = time.perf_counter()
        _infer_acc = {"queue_wait_sec": 0.0, "model_infer_sec": 0.0}
    t_wall0 = _wall_t0
    acc = _infer_acc
    assert acc is not None

    blur_eff = _blur_effective(blur_type, stage1_features)

    def _elapsed_ms() -> float:
        return (time.perf_counter() - t_wall0) * 1000.0

    response: Dict[str, Any] = {}

    def _latency_breakdown_extra(base: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        out = dict(base or {})
        tot_s = _elapsed_ms() / 1000.0
        qw = acc["queue_wait_sec"]
        mi = acc["model_infer_sec"]
        post_s = max(0.0, tot_s - qw - mi)
        out["latency_breakdown"] = {
            "queue_wait_sec": round(qw, 2),
            "model_infer_sec": round(mi, 2),
            "postprocess_sec": round(post_s, 2),
        }
        try:
            rm = dict(response.get("metadata") or {})
            pt = rm.get("prompt_eval_count") or rm.get("prompt_tokens")
            ct = rm.get("eval_count") or rm.get("completion_tokens")
            if pt is not None:
                out["prompt_tokens"] = int(pt)
            if ct is not None:
                out["completion_tokens"] = int(ct)
        except Exception:
            pass
        return out

    try:
        weights_base = ConfigLoader.get_evaluation_weights(config, blur_eff)

        if (
            prefetched_primary_response is None
            and first_entry
            and retry_count == 0
            and stage3_cache is not None
            and image_phash is not None
            and int(image_phash) != 0
        ):
            cached_raw = stage3_cache.get_cached_result(int(image_phash))
            if cached_raw is not None:
                hit_meta = dict(cached_raw.pop(CACHE_HIT_META_KEY, {}))
                logger.info(
                    "Stage3 VLM cache %s hit image=%s matched_phash=%s hamming=%s",
                    hit_meta.get("kind"),
                    Path(image_path).name,
                    hit_meta.get("matched_phash"),
                    hit_meta.get("hamming"),
                )
                cached_out = _finalize_cache_hit_result(cached_raw, blur_eff=blur_eff, hit_meta=hit_meta)
                return _attach_stage4_editing_after_stage3(
                    cached_out,
                    inference_client=inference_client,
                    config=config,
                    image_path=image_path,
                    queue_priority=queue_priority,
                    log_context=log_context,
                    inference_extra_metadata=inference_extra_metadata,
                    infer_acc=acc,
                    used_fallback_defaults=(cached_out.get("stage3_meta") or {}).get("outcome")
                    == "fallback_defaults",
                    from_cache_hit=True,
                )

        prompt_primary = build_stage3_prompt(blur_eff=blur_eff, stage1_features=stage1_features, strict_retry=False)

        if prefetched_primary_response is not None:
            response = prefetched_primary_response
            iw0 = time.perf_counter()
            iw1 = iw0
        else:
            iw0 = time.perf_counter()
            response = _run_inference_full(
                inference_client,
                image_path,
                prompt_primary,
                queue_priority,
                log_context=log_context,
                inference_extra_metadata=inference_extra_metadata,
            )
            iw1 = time.perf_counter()
        if inference_status_ok(str(response.get("status") or "")):
            qwi, mis = _timing_slice_from_response(response, iw1 - iw0)
            acc["queue_wait_sec"] += qwi
            acc["model_infer_sec"] += mis

        if not inference_status_ok(str(response.get("status") or "")):
            if retry_count < max_retries and _vlm_response_retryable(response):
                logger.warning(
                    "VLM non-success (retryable) image=%s attempt=%s err=%s",
                    Path(image_path).name,
                    retry_count,
                    response.get("error"),
                )
                _retry_sleep(retry_count)
                return analyze_with_dimensions(
                    inference_client,
                    config,
                    image_path,
                    blur_type,
                    retry_count + 1,
                    max_retries,
                    queue_priority=queue_priority,
                    stage1_features=stage1_features,
                    log_context=log_context,
                    inference_extra_metadata=inference_extra_metadata,
                    _wall_t0=t_wall0,
                    _infer_acc=acc,
                    stage3_cache=stage3_cache,
                    image_phash=image_phash,
                )
            return {
                "score": 0,
                "reason": f"API Error: {response.get('error', 'Unknown error')}",
                "tags": [],
                "dimensions": {},
                "dimensions_raw": {},
                "weakness": "",
                "dimension_comments": {},
                "editing_suggestions": [],
                "stage3_postprocess": None,
                "stage3_meta": _stage3_meta(
                    attempt=retry_count,
                    max_retries=max_retries,
                    blur_eff=blur_eff,
                    latency_ms=_elapsed_ms(),
                    outcome="vlm_error",
                    extra=_latency_breakdown_extra({"error_detail": str(response.get("error", ""))[:300]}),
                ),
                "error": True,
            }

        raw_content = response.get("text", "").strip()
        clean_json = clean_json_response(raw_content)
        parsed = parse_dimensional_response(clean_json, raw_content)

        parse_note: Dict[str, Any] = {"parse_attempts": 1}
        response_r: Dict[str, Any] | None = None

        if not parsed:
            parse_kind = classify_parse_failure(clean_json=clean_json, raw_text=raw_content)
            logger.warning(
                "Stage3 parse miss — retry prompt image=%s clean_len=%s kind=%s",
                Path(image_path).name,
                len(clean_json),
                parse_kind,
            )
            prompt_retry = build_stage3_prompt(blur_eff=blur_eff, stage1_features=stage1_features, strict_retry=True)
            iw2 = time.perf_counter()
            retry_md = dict(inference_extra_metadata or {})
            retry_md["num_predict"] = 512
            response_r = _run_inference_full(
                inference_client,
                image_path,
                prompt_retry,
                queue_priority,
                log_context=log_context,
                inference_extra_metadata=retry_md,
            )
            iw3 = time.perf_counter()
            if inference_status_ok(str(response_r.get("status") or "")):
                qwi, mis = _timing_slice_from_response(response_r, iw3 - iw2)
                acc["queue_wait_sec"] += qwi
                acc["model_infer_sec"] += mis
            parse_note["parse_attempts"] = 2
            if inference_status_ok(str(response_r.get("status") or "")):
                raw_r = response_r.get("text", "").strip()
                clean_r = clean_json_response(raw_r)
                parsed = parse_dimensional_response(clean_r, raw_r)
            else:
                logger.warning(
                    "Stage3 strict-retry inference failed image=%s err=%s",
                    Path(image_path).name,
                    response_r.get("error"),
                )

        used_fallback_defaults = False
        if not parsed:
            parsed = default_stage3_parsed()
            used_fallback_defaults = True
            parse_note["used_fallback_defaults"] = True
            logger.warning(
                "Stage3 parse exhausted — neutral fallback dimensions image=%s",
                Path(image_path).name,
            )

        outcome = "fallback_defaults" if used_fallback_defaults else "success"

        parsed = sanitize_stage3_parsed(parsed)

        src_resp = response
        if parse_note.get("parse_attempts") == 2 and response_r is not None:
            src_resp = response_r

        degraded_inf = (
            bool(src_resp.get("is_fallback"))
            or str(src_resp.get("status") or "").strip().upper() == "DEGRADED"
            or bool((src_resp.get("metadata") or {}).get("degraded"))
        )
        if degraded_inf and outcome == "success":
            outcome = "degraded_inference"

        dimensions_raw = copy_dimensions_for_audit(parsed["dimensions"])
        dimensions_cal, cal_meta = calibrate_dimension_scores(
            dimensions_raw,
            stage1_features,
            blur_eff,
        )
        weights_effective = apply_dynamic_weights(weights_base, stage1_features, blur_eff)
        total_score = weighted_ai_score(dimensions_cal, weights_effective)

        caps = cal_meta.get("caps") or []
        if caps:
            logger.info(
                "stage3_calibration image=%s dims=%s",
                Path(image_path).name,
                [c.get("dimension") for c in caps],
            )

        sa = norm_bilingual_text(parsed.get("strongest_aspect"))
        wa = norm_bilingual_text(parsed.get("weakest_aspect"))

        stage3_postprocess: Dict[str, Any] = {
            "weights_base": dict(weights_base),
            "weights_effective": weights_effective,
            "dimensions_raw": dimensions_raw,
            "calibration": cal_meta,
            "blur_effective": blur_eff,
        }

        meta_extra = _latency_breakdown_extra(dict(parse_note))

        success: Dict[str, Any] = {
            "score": total_score,
            "reason": sa["zh"] or sa["en"] or "",
            "reason_bilingual": sa,
            "tags": parsed.get("tags", []),
            "dimensions": dimensions_cal,
            "dimensions_raw": dimensions_raw,
            "weakness": wa["zh"] or wa["en"] or "",
            "weakness_bilingual": wa,
            "dimension_comments": parsed.get("dimension_comments", {}),
            "editing_suggestions": [],
            "stage3_postprocess": stage3_postprocess,
            "stage3_meta": _stage3_meta(
                attempt=retry_count,
                max_retries=max_retries,
                blur_eff=blur_eff,
                latency_ms=_elapsed_ms(),
                outcome=outcome,
                extra=meta_extra,
            ),
            "error": False,
            "inference_degraded": bool(degraded_inf and outcome == "degraded_inference"),
        }
        success = _attach_stage4_editing_after_stage3(
            success,
            inference_client=inference_client,
            config=config,
            image_path=image_path,
            queue_priority=queue_priority,
            log_context=log_context,
            inference_extra_metadata=inference_extra_metadata,
            infer_acc=acc,
            used_fallback_defaults=used_fallback_defaults,
            from_cache_hit=False,
        )
        success["stage3_meta"]["latency_ms"] = round(_elapsed_ms(), 1)
        if acc is not None:
            lb = dict((success.get("stage3_meta") or {}).get("latency_breakdown") or {})
            tot_s = _elapsed_ms() / 1000.0
            qw = acc.get("queue_wait_sec", 0.0)
            mi = acc.get("model_infer_sec", 0.0)
            lb.update(
                {
                    "queue_wait_sec": round(qw, 2),
                    "model_infer_sec": round(mi, 2),
                    "postprocess_sec": round(max(0.0, tot_s - qw - mi), 2),
                }
            )
            success["stage3_meta"]["latency_breakdown"] = lb
        attach_stage3_result(
            success,
            full_stage3_result(
                score=total_score,
                verdict=sa["zh"] or sa["en"] or "",
                dimensions_cal=dimensions_cal,
                inference_degraded=bool(success.get("inference_degraded")),
                used_fallback_defaults=used_fallback_defaults,
            ),
        )
        assert_stage3_result_consistent(success)
        if stage3_cache is not None and image_phash is not None:
            stage3_cache.store_result(int(image_phash), success)
        return success

    except Exception as e:
        if retry_count < max_retries and _exception_retryable(e):
            logger.warning(
                "Stage3 retryable error image=%s attempt=%s: %s",
                Path(image_path).name,
                retry_count,
                e,
            )
            _retry_sleep(retry_count)
            return analyze_with_dimensions(
                inference_client,
                config,
                image_path,
                blur_type,
                retry_count + 1,
                max_retries,
                queue_priority=queue_priority,
                stage1_features=stage1_features,
                log_context=log_context,
                inference_extra_metadata=inference_extra_metadata,
                _wall_t0=t_wall0,
                _infer_acc=acc,
                stage3_cache=stage3_cache,
                image_phash=image_phash,
            )

        logger.error("Analysis failed after %s retries: %s", max_retries, e)
        return {
            "score": 0,
            "reason": f"Error: {type(e).__name__}",
            "tags": [],
            "dimensions": {},
            "dimensions_raw": {},
            "weakness": "",
            "dimension_comments": {},
            "editing_suggestions": [],
            "stage3_postprocess": None,
            "stage3_meta": _stage3_meta(
                attempt=retry_count,
                max_retries=max_retries,
                blur_eff=blur_eff,
                latency_ms=_elapsed_ms(),
                outcome="exception",
                extra=_latency_breakdown_extra(
                    {"error_type": type(e).__name__, "error_msg": str(e)[:400]}
                ),
            ),
            "error": True,
        }


@dataclass
class Stage3FastFirstHooks:
    """Callbacks and logging identity for :func:`run_stage3_fast_first`."""

    append_audit_line: Callable[[str, Dict[str, Any]], None]
    progress_lock: Lock
    stats: Dict[str, Any]
    trace_id: str | None
    job_id: int | None
    session_id: int | None
    photo_id: int | None
    worker_id: int | None
    model_provider: str
    model_name: str
    pipeline_trace_session: Any | None = None
    # Optional: called once per image after audit_line so the caller can record
    # a model_runs ledger row without coupling this module to the DB layer.
    record_model_run: Optional[Callable[[str, Dict[str, Any]], None]] = None


def _append_stage3_latency_stats(hooks: Stage3FastFirstHooks, fast_s: float, full_s: float) -> None:
    """Record per-image fast, full, and total wall latency. Caller must hold ``hooks.progress_lock``."""
    record_stage3_latency_lists(hooks.stats, fast_s, full_s)


def run_stage3_fast_first(
    inference_client: Stage3InferenceClient,
    config: Dict[str, Any],
    tasks3: List[Tuple[Any, ...]],
    mw3: int,
    s3_cfg: Dict[str, Any],
    hooks: Stage3FastFirstHooks,
    *,
    logger_obj: logging.Logger | None = None,
) -> None:
    """
    Fast VLM on all pending Stage3 tasks; dimensional full pass only when
    :func:`should_run_full_after_fast` returns true (capped by ``full_analysis_top_k``).

    ``tasks3`` tuples: (idx, total_s3, file_name, file_path, tech_score, debug_info, queue_priority, stage3_cache).
    """
    log = logger_obj or logger
    from utils.logging_context import make_log_extra
    from services.processor.pipeline_image_ops import (
        fake_result_stage3_vlm_fallback,
        finalize_stage3_dual_result,
        merge_vlm_and_technical_scores,
    )
    from services.processor.pipeline_log_display import (
        build_early_reject_log_lines,
        log_stage3_image_block,
        stage3_fallback_flag,
    )

    if not tasks3:
        return

    log.debug(
        "run_stage3_fast_first inference_dispatch_parallel batch=%s mw3_hint=%s",
        len(tasks3),
        mw3,
    )

    log_ctx = {
        "trace_id": hooks.trace_id,
        "job_id": hooks.job_id,
        "session_id": hooks.session_id,
        "photo_id": hooks.photo_id,
        "worker_id": hooks.worker_id,
        "provider": hooks.model_provider,
        "model": hooks.model_name,
    }
    task_by_name: Dict[str, Tuple[Any, ...]] = {str(t[2]): t for t in tasks3}
    pending: List[Tuple[Any, ...]] = []
    fast_time_by_name: Dict[str, float] = {}
    full_time_by_name: Dict[str, float] = {}

    for t in tasks3:
        idx, total_s3, file_name, file_path, tech_score, debug_info, queue_priority, stage3_cache = t
        blur_type = (debug_info or {}).get("blur_type")
        phash = int((debug_info or {}).get("phash", 0) or 0)
        blur_eff = _blur_effective(blur_type, debug_info)
        hit = try_stage3_cache_hit_raw(
            stage3_cache,
            phash,
            blur_eff=blur_eff,
            image_path=file_path,
        )
        if hit is not None:
            t_img0 = time.perf_counter()
            log.info(
                "inference start",
                extra=make_log_extra(
                    trace_id=hooks.trace_id,
                    job_id=hooks.job_id,
                    session_id=hooks.session_id,
                    photo_id=hooks.photo_id,
                    worker_id=hooks.worker_id,
                    provider=hooks.model_provider,
                    model=hooks.model_name,
                    status="INFERENCING",
                ),
            )
            fast_c, full_c, wall_c = cache_hit_latency_triplet(hit)
            with hooks.progress_lock:
                _append_stage3_latency_stats(hooks, fast_c, full_c)
            lat_s = wall_c
            infer_latency_ms = int(round(wall_c * 1000))
            fb_log = stage3_fallback_flag(hit)
            lb = (hit.get("stage3_meta") or {}).get("latency_breakdown") or {}
            if isinstance(lb, dict) and lb.get("queue_wait_sec") is not None:
                log.info(
                    "[Stage3] image=%s total=%.2fs queue_wait=%.2fs model_infer=%.2fs postprocess=%.2fs fallback=%s",
                    file_name,
                    lat_s,
                    float(lb.get("queue_wait_sec") or 0),
                    float(lb.get("model_infer_sec") or 0),
                    float(lb.get("postprocess_sec") or 0),
                    fb_log,
                )
            else:
                log.info("[Stage3] image=%s latency=%.2fs fallback=%s", file_name, lat_s, fb_log)
            degraded_ok = bool(hit.get("inference_degraded")) or (
                (hit.get("stage3_meta") or {}).get("outcome") == "degraded_inference"
            )
            log.info(
                "inference end",
                extra=make_log_extra(
                    trace_id=hooks.trace_id,
                    job_id=hooks.job_id,
                    session_id=hooks.session_id,
                    photo_id=hooks.photo_id,
                    worker_id=hooks.worker_id,
                    provider=hooks.model_provider,
                    model=hooks.model_name,
                    status=(
                        "DEGRADED"
                        if degraded_ok
                        else ("FAILED" if hit.get("error") else "SUCCEEDED")
                    ),
                    latency_ms=infer_latency_ms,
                    error_code="INFERENCE_ERROR" if hit.get("error") else None,
                ),
            )
            smc = hit.get("stage3_meta") or {}
            if smc.get("outcome") in {
                "vlm_error",
                "parse_failed",
                "fallback_defaults",
                "degraded_inference",
            }:
                log.warning(
                    "inference retry/fallback signal",
                    extra=make_log_extra(
                        trace_id=hooks.trace_id,
                        job_id=hooks.job_id,
                        session_id=hooks.session_id,
                        worker_id=hooks.worker_id,
                        provider=hooks.model_provider,
                        model=hooks.model_name,
                        status=str(smc.get("outcome")),
                        latency_ms=smc.get("latency_ms"),
                    ),
                )

            if hit.get("error"):
                with hooks.progress_lock:
                    hooks.stats["vlm_fallback"] = hooks.stats.get("vlm_fallback", 0) + 1
                    hooks.stats["processed"] = hooks.stats.get("processed", 0) + 1
                reason_txt = (hit.get("reason") or "unknown")[:200]
                fak = fake_result_stage3_vlm_fallback(
                    tech_score=tech_score,
                    reason_txt=reason_txt,
                    debug_info=debug_info,
                )
                hooks.append_audit_line(file_path, fak)
                log.warning(
                    "\n".join(
                        build_early_reject_log_lines(
                            file_name,
                            fak,
                            progress=f"{idx}/{total_s3}",
                            config=config,
                            route_note=f"⚠️ Stage 3: {reason_txt}",
                        )
                    )
                )
                emit_stage3_partial_trace(
                    hooks.pipeline_trace_session,
                    file_name,
                    segment="stage3_fast_first_cache",
                    wall_start_mono=t_img0,
                    wall_end_mono=time.perf_counter(),
                    stage3_result=hit,
                    raw_infer_response=None,
                )
                continue

            merged_hit = merge_vlm_and_technical_scores(config, hit, tech_score, debug_info)
            hooks.append_audit_line(file_path, merged_hit)
            with hooks.progress_lock:
                hooks.stats["processed"] = hooks.stats.get("processed", 0) + 1
                if degraded_ok:
                    hooks.stats["fallback_count"] = hooks.stats.get("fallback_count", 0) + 1
            log_stage3_image_block(log, file_name, merged_hit, f"{idx}/{total_s3}", config)
            emit_stage3_partial_trace(
                hooks.pipeline_trace_session,
                file_name,
                segment="stage3_fast_first_cache",
                wall_start_mono=t_img0,
                wall_end_mono=time.perf_counter(),
                stage3_result=merged_hit,
                raw_infer_response=None,
            )
            continue

        pending.append(t)

    if not pending:
        return

    fast_by_name: Dict[str, Dict[str, Any]] = {}

    def _idle_snap() -> float | None:
        obs_fn = getattr(inference_client, "inference_queue_observability", None)
        if not callable(obs_fn):
            return None
        try:
            return float(obs_fn().get("idle_time") or 0.0)
        except Exception:
            return None

    def _max_inflight_snap() -> int | None:
        obs_fn = getattr(inference_client, "inference_queue_observability", None)
        if not callable(obs_fn):
            return None
        try:
            v = int(obs_fn().get("max_inflight") or 0)
            return v if v > 0 else None
        except Exception:
            return None

    t_batch_fast0 = time.perf_counter()
    idle_before_fast = _idle_snap()
    fut_to_task: Dict[Any, Tuple[Any, ...]] = {}
    for t in pending:
        _idx, _tot_s3, file_name, file_path, _ts, debug_info, queue_priority, _cache = t
        blur_type = (debug_info or {}).get("blur_type")
        blur_eff = _blur_effective(blur_type, debug_info)
        prompt_primary = build_stage3_fast_prompt(blur_eff=blur_eff, stage1_features=debug_info)
        itid_ff = make_image_trace_id(str(hooks.trace_id or "job"), file_name)
        inf_md_ff = merge_inference_trace_attrs(
            None,
            image_trace_id=itid_ff,
            job_trace_id=str(hooks.trace_id or ""),
            file_name=file_name,
        )
        lc_ff = {**log_ctx, "image_trace_id": itid_ff}
        fut = _infer_future_fast(
            inference_client,
            file_path,
            prompt_primary,
            queue_priority,
            int(s3_cfg["fast_num_predict"]),
            log_context=lc_ff,
            inference_extra_metadata=inf_md_ff,
        )
        fut_to_task[fut] = t

    log_stage3_inference_queue_metrics(log, inference_client, batch_phase="fast_after_submit")

    qw_accum: list[float] = []
    for fut in as_completed(fut_to_task):
        task = fut_to_task[fut]
        _idx, _tot_s3, file_name, file_path, _ts, debug_info, queue_priority, _cache = task
        blur_type = (debug_info or {}).get("blur_type")
        t_fast_wall0 = time.perf_counter()
        raw = fut.result()
        md_rw = raw.get("metadata") or {}
        if isinstance(md_rw, dict) and md_rw.get("queue_wait_sec") is not None:
            qw_accum.append(float(md_rw.get("queue_wait_sec") or 0.0))

        log.info(
            "inference start",
            extra=make_log_extra(
                trace_id=hooks.trace_id,
                job_id=hooks.job_id,
                session_id=hooks.session_id,
                photo_id=hooks.photo_id,
                worker_id=hooks.worker_id,
                provider=hooks.model_provider,
                model=hooks.model_name,
                status="INFERENCING",
            ),
        )
        fr = analyze_stage3_fast(
            inference_client,
            config,
            file_path,
            blur_type=blur_type,
            queue_priority=queue_priority,
            stage1_features=debug_info,
            log_context=log_ctx,
            fast_num_predict=int(s3_cfg["fast_num_predict"]),
            prefetched_primary_response=raw,
        )
        fast_wall_s = time.perf_counter() - t_fast_wall0
        infer_latency_ms = int(fast_wall_s * 1000)
        lat_s = fast_wall_s
        fb_log = stage3_fallback_flag(fr)
        lb = (fr.get("stage3_meta") or {}).get("latency_breakdown") or {}
        if isinstance(lb, dict) and lb.get("queue_wait_sec") is not None:
            log.info(
                "[Stage3] image=%s total=%.2fs queue_wait=%.2fs model_infer=%.2fs postprocess=%.2fs fallback=%s",
                file_name,
                lat_s,
                float(lb.get("queue_wait_sec") or 0),
                float(lb.get("model_infer_sec") or 0),
                float(lb.get("postprocess_sec") or 0),
                fb_log,
            )
        else:
            log.info("[Stage3] image=%s latency=%.2fs fallback=%s", file_name, lat_s, fb_log)

        degraded_ok = bool(fr.get("inference_degraded")) or (
            (fr.get("stage3_meta") or {}).get("outcome") == "degraded_inference"
        )
        log.info(
            "inference end",
            extra=make_log_extra(
                trace_id=hooks.trace_id,
                job_id=hooks.job_id,
                session_id=hooks.session_id,
                photo_id=hooks.photo_id,
                worker_id=hooks.worker_id,
                provider=hooks.model_provider,
                model=hooks.model_name,
                status=(
                    "DEGRADED"
                    if degraded_ok
                    else ("FAILED" if fr.get("error") else "SUCCEEDED")
                ),
                latency_ms=infer_latency_ms,
                error_code="INFERENCE_ERROR" if fr.get("error") else None,
            ),
        )
        fast_by_name[file_name] = fr
        fast_time_by_name[file_name] = lat_s
        emit_stage3_partial_trace(
            hooks.pipeline_trace_session,
            file_name,
            segment="stage3_fast_first_fast",
            wall_start_mono=t_fast_wall0,
            wall_end_mono=time.perf_counter(),
            stage3_result=fr,
            raw_infer_response=raw,
        )

    wall_fast_total = max(time.perf_counter() - t_batch_fast0, 1e-9)
    idle_after_fast = _idle_snap()
    avg_qw_fast = sum(qw_accum) / len(qw_accum) if qw_accum else None
    mi_fast = _max_inflight_snap()
    idle_delta_fast = (
        (idle_after_fast - idle_before_fast)
        if idle_before_fast is not None and idle_after_fast is not None
        else None
    )
    gpu_est_fast = (
        max(0.0, min(1.0, 1.0 - idle_delta_fast / max(float(mi_fast or 1) * wall_fast_total, 1e-9)))
        if idle_delta_fast is not None and mi_fast is not None
        else None
    )
    hooks.stats["stage3_fast_avg_queue_wait_sec"] = avg_qw_fast
    hooks.stats["stage3_fast_gpu_util_estimate"] = gpu_est_fast
    log.info(
        "stage3_inference_batch_metrics phase=fast avg_queue_wait_sec=%s gpu_util_estimate=%s wall_sec=%.3f max_inflight=%s",
        avg_qw_fast,
        gpu_est_fast,
        wall_fast_total,
        mi_fast,
    )

    log_stage3_inference_queue_metrics(log, inference_client, batch_phase="fast_batch_done")

    ordered_fast = sorted(fast_by_name.keys())
    targets, topk_dedup_metrics = pick_full_inference_targets_after_fast(
        fast_by_name,
        task_by_name,
        ordered_fast,
        max_full=int(s3_cfg["full_analysis_top_k"]),
        config=config,
    )
    if topk_dedup_metrics is not None:
        with hooks.progress_lock:
            hooks.stats["topk_dedup_before"] = topk_dedup_metrics["before_dedup"]
            hooks.stats["topk_dedup_after"] = topk_dedup_metrics["after_dedup"]
            hooks.stats["topk_dedup_removed"] = topk_dedup_metrics["removed_count"]
    target_list = [fn for fn in ordered_fast if fn in targets]

    fast_only_count = sum(
        1
        for fn in ordered_fast
        if not fast_by_name[fn].get("error") and fn not in targets
    )
    full_count = len(targets)
    denom_gate = max(1, fast_only_count + full_count)
    early_exit_ratio = fast_only_count / denom_gate
    early_pct = int(round(100.0 * early_exit_ratio))

    with hooks.progress_lock:
        hooks.stats["stage3_fast_only_count"] = fast_only_count
        hooks.stats["stage3_full_count"] = full_count
        hooks.stats["stage3_early_exit_ratio"] = early_exit_ratio

    record_stage3_early_exit_counts(fast_only=fast_only_count, full=full_count)

    compact_ff = bool((config.get("processing") or {}).get("delivery_quiet_logs"))
    if compact_ff:
        log.info(
            "Stage3 early-exit: fast_only=%s full=%s early_exit_ratio=%s%% (batch=%s)",
            fast_only_count,
            full_count,
            early_pct,
            len(pending),
        )
    else:
        log.info(
            "Stage3 early-exit:\n  fast_only: %s\n  full: %s\n  early_exit_ratio: %s%%",
            fast_only_count,
            full_count,
            early_pct,
        )
        if targets:
            log.info(
                "Stage3 fast-first: full dimensional follow-up for %s/%s images (%s)",
                len(targets),
                len(pending),
                ", ".join(sorted(targets)[:24]) + ("..." if len(targets) > 24 else ""),
            )
    full_results: Dict[str, Dict[str, Any]] = {}

    if target_list:
        t_batch_full0 = time.perf_counter()
        idle_before_full = _idle_snap()
        fut_to_fn: Dict[Any, str] = {}
        for fn in target_list:
            task = task_by_name[fn]
            _, _, file_name, file_path, _, debug_info, queue_priority, stage3_cache = task
            blur_t = (debug_info or {}).get("blur_type")
            blur_eff = _blur_effective(blur_t, debug_info)
            prompt_primary = build_stage3_prompt(blur_eff=blur_eff, stage1_features=debug_info, strict_retry=False)
            itid_fu = make_image_trace_id(str(hooks.trace_id or "job"), file_name)
            inf_md_fu = merge_inference_trace_attrs(
                None,
                image_trace_id=itid_fu,
                job_trace_id=str(hooks.trace_id or ""),
                file_name=file_name,
            )
            lc_fu = {**log_ctx, "image_trace_id": itid_fu}
            fut = _infer_future_full(
                inference_client,
                file_path,
                prompt_primary,
                queue_priority,
                log_context=lc_fu,
                inference_extra_metadata=inf_md_fu,
            )
            fut_to_fn[fut] = fn

        log_stage3_inference_queue_metrics(log, inference_client, batch_phase="full_after_submit")

        qw_full: list[float] = []
        for fut in as_completed(fut_to_fn):
            fn = fut_to_fn[fut]
            task = task_by_name[fn]
            _, _, file_name, file_path, _, debug_info, queue_priority, stage3_cache = task
            phash_i = int((debug_info or {}).get("phash", 0) or 0)
            blur_t = (debug_info or {}).get("blur_type")
            t_full_wall0 = time.perf_counter()
            raw = fut.result()
            md_rw = raw.get("metadata") or {}
            if isinstance(md_rw, dict) and md_rw.get("queue_wait_sec") is not None:
                qw_full.append(float(md_rw.get("queue_wait_sec") or 0.0))

            log.info(
                "inference start",
                extra=make_log_extra(
                    trace_id=hooks.trace_id,
                    job_id=hooks.job_id,
                    session_id=hooks.session_id,
                    photo_id=hooks.photo_id,
                    worker_id=hooks.worker_id,
                    provider=hooks.model_provider,
                    model=hooks.model_name,
                    status="INFERENCING",
                ),
            )
            full_r_inner = analyze_with_dimensions(
                inference_client,
                config,
                file_path,
                blur_type=blur_t,
                queue_priority=queue_priority,
                stage1_features=debug_info,
                log_context=log_ctx,
                inference_extra_metadata=None,
                prefetched_primary_response=raw,
                stage3_cache=stage3_cache,
                image_phash=phash_i,
            )
            full_wall_s = time.perf_counter() - t_full_wall0
            infer_latency_ms = int(full_wall_s * 1000)
            lat_s = full_wall_s
            fb_log = stage3_fallback_flag(full_r_inner)
            lb = (full_r_inner.get("stage3_meta") or {}).get("latency_breakdown") or {}
            if isinstance(lb, dict) and lb.get("queue_wait_sec") is not None:
                log.info(
                    "[Stage3] image=%s total=%.2fs queue_wait=%.2fs model_infer=%.2fs postprocess=%.2fs fallback=%s",
                    file_name,
                    lat_s,
                    float(lb.get("queue_wait_sec") or 0),
                    float(lb.get("model_infer_sec") or 0),
                    float(lb.get("postprocess_sec") or 0),
                    fb_log,
                )
            else:
                log.info("[Stage3] image=%s latency=%.2fs fallback=%s", file_name, lat_s, fb_log)

            degraded_ok = bool(full_r_inner.get("inference_degraded")) or (
                (full_r_inner.get("stage3_meta") or {}).get("outcome") == "degraded_inference"
            )
            log.info(
                "inference end",
                extra=make_log_extra(
                    trace_id=hooks.trace_id,
                    job_id=hooks.job_id,
                    session_id=hooks.session_id,
                    photo_id=hooks.photo_id,
                    worker_id=hooks.worker_id,
                    provider=hooks.model_provider,
                    model=hooks.model_name,
                    status=(
                        "DEGRADED"
                        if degraded_ok
                        else ("FAILED" if full_r_inner.get("error") else "SUCCEEDED")
                    ),
                    latency_ms=infer_latency_ms,
                    error_code="INFERENCE_ERROR" if full_r_inner.get("error") else None,
                ),
            )
            smf = full_r_inner.get("stage3_meta") or {}
            if smf.get("outcome") in {
                "vlm_error",
                "parse_failed",
                "fallback_defaults",
                "degraded_inference",
            }:
                log.warning(
                    "inference retry/fallback signal",
                    extra=make_log_extra(
                        trace_id=hooks.trace_id,
                        job_id=hooks.job_id,
                        session_id=hooks.session_id,
                        worker_id=hooks.worker_id,
                        provider=hooks.model_provider,
                        model=hooks.model_name,
                        status=str(smf.get("outcome")),
                        latency_ms=smf.get("latency_ms"),
                    ),
                )
            full_results[fn] = full_r_inner
            full_time_by_name[fn] = lat_s
            emit_stage3_partial_trace(
                hooks.pipeline_trace_session,
                file_name,
                segment="stage3_fast_first_full",
                wall_start_mono=t_full_wall0,
                wall_end_mono=time.perf_counter(),
                stage3_result=full_r_inner,
                raw_infer_response=raw,
            )

        wall_full_total = max(time.perf_counter() - t_batch_full0, 1e-9)
        idle_after_full = _idle_snap()
        avg_qw_full = sum(qw_full) / len(qw_full) if qw_full else None
        mi_full = _max_inflight_snap()
        idle_delta_full = (
            (idle_after_full - idle_before_full)
            if idle_before_full is not None and idle_after_full is not None
            else None
        )
        gpu_est_full = (
            max(0.0, min(1.0, 1.0 - idle_delta_full / max(float(mi_full or 1) * wall_full_total, 1e-9)))
            if idle_delta_full is not None and mi_full is not None
            else None
        )
        hooks.stats["stage3_full_avg_queue_wait_sec"] = avg_qw_full
        hooks.stats["stage3_full_gpu_util_estimate"] = gpu_est_full
        log.info(
            "stage3_inference_batch_metrics phase=full avg_queue_wait_sec=%s gpu_util_estimate=%s wall_sec=%.3f max_inflight=%s",
            avg_qw_full,
            gpu_est_full,
            wall_full_total,
            mi_full,
        )

        log_stage3_inference_queue_metrics(log, inference_client, batch_phase="full_batch_done")

    for fn in ordered_fast:
        fr = fast_by_name[fn]
        task = task_by_name[fn]
        idx, total_s3, file_name, file_path, tech_score, debug_info, _queue_pri, _s3cache = task
        prog = f"{idx}/{total_s3}"

        if fr.get("error"):
            fast_s = fast_time_by_name[fn]
            with hooks.progress_lock:
                hooks.stats["vlm_fallback"] = hooks.stats.get("vlm_fallback", 0) + 1
                hooks.stats["processed"] = hooks.stats.get("processed", 0) + 1
                _append_stage3_latency_stats(hooks, fast_s, 0.0)
            reason_txt = (fr.get("reason") or "unknown")[:200]
            fak = fake_result_stage3_vlm_fallback(
                tech_score=tech_score,
                reason_txt=reason_txt,
                debug_info=debug_info,
            )
            hooks.append_audit_line(file_path, fak)
            if hooks.record_model_run is not None:
                try:
                    hooks.record_model_run(file_path, fak)
                except Exception:
                    pass
            log.warning(
                "\n".join(
                    build_early_reject_log_lines(
                        file_name,
                        fak,
                        progress=prog,
                        config=config,
                        route_note=f"⚠️ Stage 3: {reason_txt}",
                    )
                )
            )
            continue

        full_r: Dict[str, Any] | None = full_results.get(fn) if fn in targets else None

        if full_r is not None and not full_r.get("error"):
            merged = finalize_stage3_dual_result(
                config=config,
                tech_score=tech_score,
                debug_info=debug_info,
                fast_inner=fr,
                full_inner=full_r,
            )
        else:
            merged = finalize_stage3_dual_result(
                config=config,
                tech_score=tech_score,
                debug_info=debug_info,
                fast_inner=fr,
                full_inner=None,
            )
            if full_r is not None and full_r.get("error"):
                with hooks.progress_lock:
                    hooks.stats["vlm_fallback"] = hooks.stats.get("vlm_fallback", 0) + 1

        hooks.append_audit_line(file_path, merged)
        if hooks.record_model_run is not None:
            try:
                hooks.record_model_run(file_path, merged)
            except Exception:
                pass
        fast_s = fast_time_by_name[fn]
        full_s = float(full_time_by_name.get(fn, 0.0))
        with hooks.progress_lock:
            hooks.stats["processed"] = hooks.stats.get("processed", 0) + 1
            degraded_final = bool(merged.get("inference_degraded")) or (
                (merged.get("stage3_meta") or {}).get("outcome") == "degraded_inference"
            )
            if degraded_final:
                hooks.stats["fallback_count"] = hooks.stats.get("fallback_count", 0) + 1
            _append_stage3_latency_stats(hooks, fast_s, full_s)
        log_stage3_image_block(log, file_name, merged, prog, config)
