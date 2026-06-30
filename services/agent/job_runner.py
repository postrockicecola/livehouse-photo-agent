"""Bridge between the SSOT job system and the curation agent.

``JobExecutor`` calls :func:`run_curation_job` for ``CURATE_*`` jobs. This module:

- builds the agent from the job payload (same inputs as the offline CLI);
- streams each ANALYZE / FINALIZE decision into ``job_events`` (committed live) so
  the Infra Console timeline renders the agent loop step by step — the same
  surface the rest of the executor uses, not a parallel one;
- writes a ``curation_result.json`` artifact next to the source images;
- returns a summary dict (metrics + selection) for the success payload.

Heavy model imports stay lazy: tests inject ``analyze_fn`` and never touch a GPU.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Mapping, Optional

from services.agent.loop import CurationAgent
from services.agent.session import agent_config_from_payload, ensure_step_budget, load_candidates
from services.agent.tools import AnalyzeFn, AnalyzeTool, FinalizeTool, InspectTool, ToolRegistry
from services.agent.types import ActionType, AgentResult, AgentState, AgentStep

logger = logging.getLogger(__name__)

CURATE_JOB_TYPES = ("CURATE_PATH", "CURATE_SESSION")


def run_curation_job(
    conn: Any,
    *,
    job_id: int,
    source_dir: str,
    trace_id: str,
    config_path: str = "configs/livehouse.yaml",
    payload: Mapping[str, Any] | None = None,
    analyze_fn: Optional[AnalyzeFn] = None,
    write_artifact: bool = True,
) -> dict[str, Any]:
    """Run the curation agent for one job; emit events, write artifact, return summary."""
    config = agent_config_from_payload(payload)
    candidates = load_candidates(source_dir)
    if not candidates:
        raise FileNotFoundError(f"curation job {job_id}: no candidate images under {source_dir!r}")
    ensure_step_budget(config, len(candidates))

    if analyze_fn is None:
        from services.agent.tools import build_stage3_analyze_fn

        analyze_fn = build_stage3_analyze_fn(
            config_path=config_path,
            source_dir=source_dir,
            trace_id=trace_id,
            job_id=job_id,
        )

    tools = ToolRegistry(
        inspect=InspectTool(),
        analyze=AnalyzeTool(analyze_fn, default_tier=config.base_tier),
        finalize=FinalizeTool(),
    )
    agent = CurationAgent(
        tools=tools,
        config=config,
        planner=_build_planner(payload, config_path=config_path),
        step_hook=_make_event_step_hook(conn, job_id=job_id, trace_id=trace_id),
    )
    result = agent.run(candidates)

    artifact_path: str | None = None
    if write_artifact:
        artifact_path = _write_curation_artifact(source_dir, result, trace_id=trace_id)

    logger.info(
        "curation agent finished job=%s selected=%s steps=%s inferences=%s escalations=%s",
        job_id,
        result.metrics.get("selected_count"),
        result.metrics.get("steps"),
        result.metrics.get("inferences_used"),
        result.metrics.get("escalations"),
    )
    return {
        "metrics": result.metrics,
        "selected": result.selected,
        "candidate_count": len(candidates),
        "curation_artifact": artifact_path,
        "selection": _selection_summary(result),
    }


def _build_planner(payload: Mapping[str, Any] | None, *, config_path: str):
    """Select the planner from the job payload: ``agent.planner == "llm"`` opts into
    the LLM tool-calling planner over the configured provider (heuristic fallback);
    anything else (default) uses the deterministic heuristic planner.

    The LLM backend import is lazy so heuristic jobs and tests never touch it.
    """
    agent_cfg = (payload or {}).get("agent") or {}
    planner_kind = str(agent_cfg.get("planner") or "heuristic").strip().lower()
    if planner_kind != "llm":
        return None
    try:
        from services.agent.llm_backend import build_curation_llm_planner_from_config

        return build_curation_llm_planner_from_config(
            config_path, model_name=(agent_cfg.get("planner_model") or None)
        )
    except Exception:
        logger.exception("failed to build LLM planner; falling back to heuristic")
        return None


def _make_event_step_hook(conn: Any, *, job_id: int, trace_id: str):
    """Step hook that records ANALYZE / FINALIZE decisions as committed ``job_events``.

    INSPECT steps are skipped to keep the timeline bounded (there can be hundreds);
    ANALYZE events are bounded by the inference budget.
    """
    from utils.luma_brain import append_job_event

    def hook(step: AgentStep, state: AgentState) -> None:
        call = step.call
        if call.action == ActionType.ANALYZE:
            obs = step.result.observation or {}
            message = (
                f"agent analyze {call.image_id} tier={obs.get('tier')} "
                f"score={obs.get('score')} conf={obs.get('confidence')}"
                + (" [escalated]" if call.source == "reflection" else "")
            )
            event_payload: dict[str, Any] = {
                "agent_action": "analyze",
                "image_id": call.image_id,
                "tier": obs.get("tier"),
                "score": obs.get("score"),
                "confidence": obs.get("confidence"),
                "ok": step.result.ok,
                "source": call.source,
                "reason": call.reason,
                "reflection": step.reflection,
                "step": step.index,
                "inferences_used": state.inferences_used,
                "latency_ms": step.result.latency_ms,
                "trace_id": trace_id,
            }
        elif call.action == ActionType.FINALIZE:
            obs = step.result.observation or {}
            message = f"agent finalize selected={obs.get('count')}"
            event_payload = {
                "agent_action": "finalize",
                "selected": obs.get("selected"),
                "source": call.source,
                "reason": call.reason,
                "step": step.index,
                "trace_id": trace_id,
            }
        else:
            return

        try:
            append_job_event(conn, job_id=job_id, to_status=None, message=message, payload=event_payload)
            conn.commit()  # commit so the Infra Console sees the step live, mid-run
        except Exception:
            logger.exception("failed to write agent job_event (step #%s)", step.index)

    return hook


def _selection_summary(result: AgentResult) -> list[dict[str, Any]]:
    by_id = {c.image_id: c for c in result.candidates}
    out: list[dict[str, Any]] = []
    for cid in result.selected:
        c = by_id.get(cid)
        if c is None:
            continue
        out.append(
            {
                "image_id": c.image_id,
                "score": c.score,
                "confidence": c.confidence,
                "tier": c.tier,
                "escalated": c.escalated,
            }
        )
    return out


def _write_curation_artifact(source_dir: str, result: AgentResult, *, trace_id: str) -> str | None:
    path = Path(source_dir) / "curation_result.json"
    body = {
        "trace_id": trace_id,
        "generated_at": int(time.time()),
        "metrics": result.metrics,
        "selected": result.selected,
        "selection": _selection_summary(result),
        "candidates": [
            {
                "image_id": c.image_id,
                "fast_score": c.fast_score(),
                "score": c.score,
                "confidence": c.confidence,
                "tier": c.tier,
                "escalated": c.escalated,
                "attempts": c.attempts,
            }
            for c in result.candidates
        ],
    }
    try:
        path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(path)
    except OSError:
        logger.exception("failed to write curation artifact to %s", path)
        return None
