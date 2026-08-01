"""Phase-6 quality rubrics: human ratings + weak LLM-judge (groundedness-gated).

Three 1–5 scores only: useful / honest / concise.
An LLM judge is assistive — ``grounded_ok`` from tool-file cites always wins.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from services.agent.groundedness import (
    collect_allowed_files,
    extract_file_mentions,
    should_enforce_groundedness,
)

ChatFn = Callable[[list[dict[str, str]]], str]

SCORE_KEYS = ("useful", "honest", "concise")
DEFAULT_PASS_MIN = 3

_JUDGE_SYSTEM = (
    "You are grading a Gallery Copilot turn for a livehouse photo curation app. "
    "Score ONLY these dimensions from 1 (bad) to 5 (excellent):\n"
    "- useful: did the reply help the user's curation ask?\n"
    "- honest: no invented files/scores; empty results admitted?\n"
    "- concise: short and clear without filler?\n"
    "Reply with ONLY JSON: "
    '{"useful":N,"honest":N,"concise":N,"rationale":"one short sentence"}'
)


@dataclass
class RubricScores:
    useful: int
    honest: int
    concise: int

    def as_dict(self) -> dict[str, int]:
        return {"useful": self.useful, "honest": self.honest, "concise": self.concise}

    def mean(self) -> float:
        return round((self.useful + self.honest + self.concise) / 3.0, 3)


@dataclass
class JudgeVerdict:
    scores: RubricScores
    grounded_ok: bool
    pass_: bool
    rationale: str = ""
    rater: str = "llm_judge"
    gated: bool = False  # True when groundedness lowered honest / failed the turn
    raw_scores: dict[str, int] = field(default_factory=dict)
    detail: dict[str, Any] = field(default_factory=dict)

    def to_rating_row(
        self,
        *,
        case_id: str,
        utterance: str,
        reply: str,
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return {
            "schema_version": "agent_rating.v1",
            "id": f"{case_id}__{self.rater}",
            "case_id": case_id,
            "utterance": utterance,
            "reply": (reply or "")[:2000],
            "tool_calls": [
                {"tool": tc.get("tool"), "ok": tc.get("ok")} for tc in (tool_calls or [])
            ],
            "scores": self.scores.as_dict(),
            "raw_scores": dict(self.raw_scores),
            "mean": self.scores.mean(),
            "grounded_ok": self.grounded_ok,
            "pass": self.pass_,
            "gated": self.gated,
            "rationale": self.rationale,
            "rater": self.rater,
            "detail": dict(self.detail),
        }


def clamp_score(value: Any, *, default: int = 3) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(5, n))


def parse_judge_json(text: str) -> tuple[RubricScores, str]:
    """Extract rubric JSON from a judge model reply (tolerant of fences)."""
    s = (text or "").strip()
    if s.startswith("```"):
        s = s.strip("`")
        nl = s.find("\n")
        if nl != -1:
            s = s[nl + 1 :]
    start, end = s.find("{"), s.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("judge reply has no JSON object")
    obj = json.loads(s[start : end + 1])
    if not isinstance(obj, dict):
        raise ValueError("judge JSON must be an object")
    scores = RubricScores(
        useful=clamp_score(obj.get("useful")),
        honest=clamp_score(obj.get("honest")),
        concise=clamp_score(obj.get("concise")),
    )
    rationale = str(obj.get("rationale") or "")[:400]
    return scores, rationale


def check_reply_groundedness(
    reply: str,
    *,
    tool_calls: list[dict[str, Any]] | None = None,
    working_memory: dict[str, Any] | None = None,
) -> tuple[bool, list[str]]:
    """Return ``(ok, unknown_basenames)`` using the same cite rules as production."""
    if not should_enforce_groundedness(tool_calls, working_memory=working_memory):
        return True, []
    allowed = collect_allowed_files(tool_calls, working_memory=working_memory)
    cited = extract_file_mentions(reply or "")
    unknown = [c for c in cited if c not in allowed]
    return (not unknown), unknown


def apply_groundedness_gate(
    scores: RubricScores,
    *,
    grounded_ok: bool,
    pass_min: int = DEFAULT_PASS_MIN,
) -> tuple[RubricScores, bool, bool]:
    """Force honesty down and fail the turn when cites are ungrounded.

    Returns ``(possibly_adjusted_scores, pass, gated)``.
    """
    gated = False
    useful, honest, concise = scores.useful, scores.honest, scores.concise
    if not grounded_ok:
        gated = True
        honest = min(honest, 1)
    adjusted = RubricScores(useful=useful, honest=honest, concise=concise)
    passed = (
        grounded_ok
        and adjusted.useful >= pass_min
        and adjusted.honest >= pass_min
        and adjusted.concise >= pass_min
    )
    return adjusted, passed, gated


def build_judge_messages(
    *,
    utterance: str,
    reply: str,
    tool_calls: list[dict[str, Any]] | None = None,
    observations: list[str] | None = None,
) -> list[dict[str, str]]:
    tools_summary = json.dumps(
        [{"tool": tc.get("tool"), "ok": tc.get("ok"), "args": tc.get("args")} for tc in (tool_calls or [])],
        ensure_ascii=False,
    )[:4000]
    obs = "\n".join(observations or [])[:4000] or "(none)"
    user = (
        f"User ask:\n{utterance}\n\n"
        f"Tools called:\n{tools_summary}\n\n"
        f"Tool observations (abbrev):\n{obs}\n\n"
        f"Assistant reply:\n{reply}\n\n"
        "Score useful/honest/concise now."
    )
    return [
        {"role": "system", "content": _JUDGE_SYSTEM},
        {"role": "user", "content": user},
    ]


def judge_turn(
    chat_fn: ChatFn,
    *,
    utterance: str,
    reply: str,
    tool_calls: list[dict[str, Any]] | None = None,
    working_memory: dict[str, Any] | None = None,
    observations: list[str] | None = None,
    pass_min: int = DEFAULT_PASS_MIN,
    rater: str = "llm_judge",
) -> JudgeVerdict:
    """Run LLM judge then apply groundedness hard gate."""
    grounded_ok, unknown = check_reply_groundedness(
        reply, tool_calls=tool_calls, working_memory=working_memory
    )
    messages = build_judge_messages(
        utterance=utterance,
        reply=reply,
        tool_calls=tool_calls,
        observations=observations,
    )
    raw = chat_fn(messages)
    try:
        scores, rationale = parse_judge_json(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        # Unparseable judge → conservative mid scores, still apply groundedness.
        scores = RubricScores(useful=3, honest=3, concise=3)
        rationale = f"judge_parse_failed: {exc}"
    raw_scores = scores.as_dict()
    final, passed, gated = apply_groundedness_gate(
        scores, grounded_ok=grounded_ok, pass_min=pass_min
    )
    return JudgeVerdict(
        scores=final,
        grounded_ok=grounded_ok,
        pass_=passed,
        rationale=rationale,
        rater=rater,
        gated=gated,
        raw_scores=raw_scores,
        detail={"unknown_files": unknown, "judge_raw": (raw or "")[:500]},
    )


def human_rating(
    scores: dict[str, Any],
    *,
    reply: str,
    tool_calls: list[dict[str, Any]] | None = None,
    working_memory: dict[str, Any] | None = None,
    pass_min: int = DEFAULT_PASS_MIN,
    rationale: str = "",
) -> JudgeVerdict:
    """Validate a human 1–5 rubric and apply the same groundedness gate."""
    rubric = RubricScores(
        useful=clamp_score(scores.get("useful")),
        honest=clamp_score(scores.get("honest")),
        concise=clamp_score(scores.get("concise")),
    )
    grounded_ok, unknown = check_reply_groundedness(
        reply, tool_calls=tool_calls, working_memory=working_memory
    )
    final, passed, gated = apply_groundedness_gate(
        rubric, grounded_ok=grounded_ok, pass_min=pass_min
    )
    return JudgeVerdict(
        scores=final,
        grounded_ok=grounded_ok,
        pass_=passed,
        rationale=rationale,
        rater="human",
        gated=gated,
        raw_scores=rubric.as_dict(),
        detail={"unknown_files": unknown},
    )


def rating_to_review_stub(row: dict[str, Any]) -> dict[str, Any]:
    """Turn a failing rating into a review-queue annotation stub for promotion."""
    return {
        "conversation_id": row.get("conversation_id") or 0,
        "reasons": ["judge_fail"] + (["grounding_violation"] if not row.get("grounded_ok") else []),
        "user_text": row.get("utterance") or "",
        "issue_type": "hallucination" if not row.get("grounded_ok") else "style_violation",
        "expected_behavior": {
            "should_route": None,
            "rule_id": None,
            "promote_agent_case": True,
        },
        "action": "add_regression_test",
        "notes": f"judge:{row.get('rationale') or ''} scores={row.get('scores')}",
        "reviewed_by": row.get("rater") or "llm_judge",
        "reviewed_at": "",
        "turn": {
            "reply": row.get("reply"),
            "tool_calls": row.get("tool_calls") or [],
            "trace": {"grounding_ok": row.get("grounded_ok")},
        },
    }
