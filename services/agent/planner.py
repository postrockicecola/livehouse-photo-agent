"""Planners decide the next tool call. Two implementations:

- :class:`HeuristicPlanner` — deterministic, budget-aware policy. Always available,
  fully unit-testable, and the safety net behind the LLM planner.
- :class:`LLMPlanner` — real LLM tool-calling: it serializes the agent state + tool
  schema into a prompt, asks the model for the next action as JSON, and parses it.
  Malformed / out-of-contract output falls back to the heuristic planner — the same
  structured-output-reliability pattern the rest of the pipeline uses.

The planner is the "brain" of the ReAct loop; tools are the "hands".
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Optional, Protocol

from services.agent.types import ActionType, AgentState, ToolCall

logger = logging.getLogger(__name__)

# A text-completion function: prompt -> raw model text. Lets the planner stay
# decoupled from any specific provider/transport (and trivially fakeable in tests).
CompleteFn = Callable[[str], str]


class Planner(Protocol):
    def next_action(self, state: AgentState) -> ToolCall: ...


def _pop_escalation_call(state: AgentState) -> Optional[ToolCall]:
    """Deterministic self-correction step: pop the next queued escalation, if affordable.

    Shared by the heuristic and LLM planners so reflection-driven escalations behave
    identically regardless of who is choosing the *next* analyze target. Returns None
    when there is nothing to escalate (or the budget is spent).
    """
    cfg = state.config
    while state.pending_escalations:
        if state.budget_exhausted():
            state.pending_escalations.clear()
            return None
        img = state.pending_escalations[0]
        cand = state.candidates.get(img)
        if cand is None or cand.escalated:
            state.pending_escalations.pop(0)
            continue
        state.pending_escalations.pop(0)
        return ToolCall(
            action=ActionType.ANALYZE,
            image_id=img,
            args={"tier": cfg.escalation_tier},
            reason=f"escalate {img} to {cfg.escalation_tier} after reflection",
            source="reflection",
        )
    return None


class HeuristicPlanner:
    """Deterministic policy: clear escalations → inspect → analyze best → finalize."""

    def next_action(self, state: AgentState) -> ToolCall:
        cfg = state.config

        # 1) Honor reflection-requested escalations first (if we can still pay).
        esc = _pop_escalation_call(state)
        if esc is not None:
            return esc

        # 2) Cheaply inspect anything we haven't looked at yet.
        not_inspected = state.not_inspected()
        if not_inspected:
            c = not_inspected[0]
            return ToolCall(
                action=ActionType.INSPECT,
                image_id=c.image_id,
                reason=f"inspect cheap features for {c.image_id}",
            )

        # 3) Deep-analyze the strongest un-analyzed candidate, budget permitting.
        if state.can_analyze_more():
            pool = state.analyzable()
            if pool:
                c = max(pool, key=lambda x: x.fast_score())
                return ToolCall(
                    action=ActionType.ANALYZE,
                    image_id=c.image_id,
                    args={"tier": cfg.base_tier},
                    reason=f"analyze top fast_score candidate {c.image_id} ({c.fast_score():.1f})",
                )

        # 4) Nothing left to do affordably → commit.
        return ToolCall(
            action=ActionType.FINALIZE,
            reason="no affordable work remaining; commit keepers",
        )


class LLMPlanner:
    """LLM tool-calling planner with a deterministic fallback on bad output.

    With ``auto_inspect=True`` (default) the planner only spends an LLM call on the
    *high-value* decisions — which photo to deep-analyze next, and when to finalize.
    The mechanical, zero-cost steps (cheap INSPECT of every candidate) and the
    reflection-driven escalations are handled deterministically, so the model is not
    asked to micromanage grunt work it tends to loop on. This keeps the LLM in charge
    of judgement, not bookkeeping, and makes ``llm_decision_rate`` reflect real
    decisions rather than being diluted by inspect chatter.
    """

    def __init__(
        self,
        complete_fn: CompleteFn,
        *,
        fallback: Optional[Planner] = None,
        max_state_candidates: int = 40,
        auto_inspect: bool = True,
    ) -> None:
        self._complete = complete_fn
        self._fallback = fallback or HeuristicPlanner()
        self._max_state_candidates = max_state_candidates
        self._auto_inspect = auto_inspect

    def next_action(self, state: AgentState) -> ToolCall:
        if self._auto_inspect:
            # Deterministic, no-LLM steps first: escalations, then cheap inspection.
            esc = _pop_escalation_call(state)
            if esc is not None:
                return esc
            not_inspected = state.not_inspected()
            if not_inspected:
                c = not_inspected[0]
                return ToolCall(
                    action=ActionType.INSPECT,
                    image_id=c.image_id,
                    reason=f"auto-inspect {c.image_id}",
                    source="auto_inspect",
                )

        ordered = state.ordered_candidates()
        prompt = self.build_prompt(state, ordered)
        try:
            raw = self._complete(prompt)
        except Exception as exc:
            logger.warning("LLM planner completion failed (%s); using fallback", exc)
            return self._fallback_call(state, "llm_completion_error")

        parsed = _extract_json_object(raw)
        if parsed is None:
            logger.info("LLM planner returned non-JSON; using fallback")
            return self._fallback_call(state, "llm_unparseable")

        call = self._coerce_call(parsed, state, ordered)
        if call is None:
            logger.info("LLM planner action out of contract; using fallback")
            return self._fallback_call(state, "llm_invalid_action")
        return call

    def build_prompt(self, state: AgentState, ordered: Optional[list] = None) -> str:
        cfg = state.config
        if ordered is None:
            ordered = state.ordered_candidates()
        rows = []
        # Candidates are referenced by a small integer ``idx`` (their position here),
        # NOT by filename: small models reliably emit "idx": 3 but routinely garble a
        # 20-char image id, which silently breaks tool-calling. ``_coerce_call`` maps
        # the idx back to the real image_id.
        for i, c in enumerate(ordered[: self._max_state_candidates]):
            row = {
                "idx": i,
                "inspected": c.inspected,
                "fast_score": round(c.fast_score(), 1),
                "analyzed": c.analyzed,
                "score": c.score,
                "confidence": c.confidence,
                "tier": c.tier,
            }
            # Only surface cluster grouping once the CLUSTER tool has run (keeps the
            # pre-cluster prompt clean; lets the model analyze one rep per burst after).
            if state.clustered and "cluster_id" in c.features:
                row["cluster"] = c.features.get("cluster_id")
                row["rep"] = bool(c.features.get("cluster_rep"))
            rows.append(row)
        tools = [
            {"action": "inspect", "args": {"idx": "int"}},
            {"action": "analyze", "args": {"idx": "int", "tier": "fast|full"}},
            {"action": "compare", "args": {"a": "int idx", "b": "int idx"}},
            {"action": "cluster", "args": {}},
            {"action": "query_gallery", "args": {"idx": "int"}},
            {"action": "finalize", "args": {"selected": "optional [int idx]"}},
        ]
        budget = {
            "inferences_used": state.inferences_used,
            "max_inferences": cfg.max_inferences,
            "target_keepers": cfg.target_keepers,
            "keep_score_threshold": cfg.keep_score_threshold,
            "pending_escalations_idx": [
                i for i, c in enumerate(ordered) if c.image_id in state.pending_escalations
            ],
        }
        return (
            "You are a concert-photo culling agent. Pick photos worth delivering.\n"
            "Refer to a photo only by its integer 'idx' from CANDIDATES below.\n"
            "Choose exactly ONE next action and reply with a single JSON object, e.g.:\n"
            '{"action": "analyze", "idx": <int>, "tier": "fast|full", "reason": "..."}\n'
            "Rules:\n"
            "- NEVER inspect a photo whose inspected=true; pick one with inspected=false.\n"
            "- Once every photo is inspected, ANALYZE the most promising un-analyzed ones\n"
            "  (analyzed=false), preferring higher fast_score.\n"
            "- Each analyze costs 1 inference; the zero-cost tools do not.\n"
            "- Optional CLUSTER (once) groups burst/near-duplicate frames and adds\n"
            "  'cluster'/'rep' to CANDIDATES; then prefer analyzing one rep=true per cluster.\n"
            "- Optional QUERY_GALLERY recalls a prior score for one image (it becomes\n"
            "  analyzed=true with tier='recall', no inference) — reuse it instead of analyzing.\n"
            "- Optional COMPARE weighs two candidates before spending an analyze.\n"
            "- FINALIZE once inferences_used reaches max_inferences or you have enough\n"
            "  keepers above keep_score_threshold.\n"
            "- Do not repeat an action that makes no progress.\n\n"
            f"TOOLS: {json.dumps(tools)}\n"
            f"BUDGET: {json.dumps(budget)}\n"
            f"CANDIDATES: {json.dumps(rows)}\n"
            "JSON action:"
        )

    def _coerce_call(
        self, parsed: dict[str, Any], state: AgentState, ordered: list
    ) -> Optional[ToolCall]:
        action_raw = str(parsed.get("action", "")).strip().lower()
        try:
            action = ActionType(action_raw)
        except ValueError:
            return None
        reason = str(parsed.get("reason") or "")[:200]
        if action == ActionType.FINALIZE:
            selected = _resolve_selected(parsed.get("selected"), state, ordered)
            args = {"selected": selected} if selected else {}
            return ToolCall(action=action, args=args, reason=reason, source="llm")
        if action == ActionType.CLUSTER:
            # One-shot: re-clustering is a no-op → treat as out-of-contract so the loop advances.
            if state.clustered:
                return None
            return ToolCall(action=action, reason=reason, source="llm")
        if action == ActionType.COMPARE:
            a_id = _idx_to_image_id(parsed.get("a"), ordered)
            b_id = _idx_to_image_id(parsed.get("b"), ordered)
            if a_id is None or b_id is None or a_id == b_id:
                return None
            return ToolCall(
                action=action, args={"a_id": a_id, "b_id": b_id}, reason=reason, source="llm"
            )
        if action == ActionType.QUERY_GALLERY:
            image_id = _resolve_image_id(parsed, state, ordered)
            if image_id is None:
                return None
            cand = state.candidates.get(image_id)
            # Re-querying the same image (or one already analyzed) makes no progress.
            if cand is not None and (cand.features.get("gallery_queried") or cand.analyzed):
                return None
            return ToolCall(action=action, image_id=image_id, reason=reason, source="llm")
        image_id = _resolve_image_id(parsed, state, ordered)
        if image_id is None:
            return None
        # Progress guard: reject no-op actions so a looping LLM (e.g. re-inspecting an
        # already-inspected photo, or re-analyzing at the same tier) is treated as
        # out-of-contract and the deterministic fallback advances the loop instead.
        cand = state.candidates.get(image_id)
        if action == ActionType.INSPECT and cand is not None and cand.inspected:
            return None
        args: dict[str, Any] = {}
        if action == ActionType.ANALYZE:
            tier = str(parsed.get("tier") or state.config.base_tier)
            if cand is not None and cand.analysis is not None and cand.tier == tier:
                return None
            args["tier"] = tier
        return ToolCall(action=action, image_id=image_id, args=args, reason=reason, source="llm")

    def _fallback_call(self, state: AgentState, why: str) -> ToolCall:
        call = self._fallback.next_action(state)
        call.source = "llm_fallback"
        call.reason = f"[{why}] {call.reason}"
        return call


def _as_index(value: Any) -> Optional[int]:
    """Coerce an LLM-supplied idx (int or numeric string) to an int; reject bools."""
    if isinstance(value, bool):  # bool is an int subclass — never a valid idx
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def _idx_to_image_id(value: Any, ordered: list) -> Optional[str]:
    """Map a bare integer ``idx`` (used by COMPARE's a/b) to a real image_id."""
    idx = _as_index(value)
    if idx is not None and 0 <= idx < len(ordered):
        return ordered[idx].image_id
    return None


def _resolve_image_id(parsed: dict[str, Any], state: AgentState, ordered: list) -> Optional[str]:
    """Map a planner reference to a real image_id: prefer integer ``idx``, else ``image_id``."""
    idx = _as_index(parsed.get("idx"))
    if idx is not None and 0 <= idx < len(ordered):
        return ordered[idx].image_id
    image_id = parsed.get("image_id")
    if isinstance(image_id, str) and image_id in state.candidates:
        return image_id
    return None


def _resolve_selected(value: Any, state: AgentState, ordered: list) -> list[str]:
    """Resolve a finalize ``selected`` list of idx ints (or image_ids) to image_ids."""
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        idx = _as_index(item)
        if idx is not None and 0 <= idx < len(ordered):
            out.append(ordered[idx].image_id)
        elif isinstance(item, str) and item in state.candidates:
            out.append(item)
    return out


def _extract_json_object(text: str) -> Optional[dict[str, Any]]:
    """Tolerant single-object JSON extraction from possibly chatty LLM output."""
    if not text:
        return None
    s = text.strip()
    if s.startswith("```"):
        s = s.strip("`")
        nl = s.find("\n")
        if nl != -1:
            s = s[nl + 1 :]
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        obj = json.loads(s[start : end + 1])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None
