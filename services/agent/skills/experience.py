"""Owner-scoped selection feedback and Experience RAG skills."""
from __future__ import annotations

import re
from typing import Any

from services.agent import store
from services.agent.skills.base import SkillRegistry, SkillResult
from services.knowledge_index import bm25_scores, tokenize

_REASON_PATTERNS = (
    ("too_dark", re.compile(r"(太暗|欠曝|看不清|underexpos)", re.IGNORECASE)),
    ("blurry", re.compile(r"(太糊|模糊|失焦|blurr|out.?of.?focus)", re.IGNORECASE)),
    ("weak_moment", re.compile(r"(没张力|瞬间不好|动作不好|表情不好|weak.?moment)", re.IGNORECASE)),
    ("poor_subject", re.compile(r"(遮挡|主体不完整|脸看不清|poor.?subject)", re.IGNORECASE)),
)


def infer_feedback_reason(feedback: str) -> str:
    return next(
        (reason for reason, pattern in _REASON_PATTERNS if pattern.search(feedback or "")),
        "other",
    )


class RecordSelectionFeedbackSkill:
    name = "record_selection_feedback"
    description = (
        "Persist explicit user feedback about selected photos for future personalized retrieval. "
        "Use when the user accepts/rejects a frame or says it is too dark, blurry, weak, or blocked."
    )
    parameters = {
        "type": "object",
        "properties": {
            "feedback": {"type": "string"},
            "decision": {"type": "string", "enum": ["accepted", "rejected"]},
            "query": {"type": "string"},
            "files": {"type": "array", "items": {"type": "string"}},
            "selection_id": {"type": "string"},
            "subject": {"type": "string"},
            "style": {"type": "string"},
            "platform": {"type": "string"},
            "reason_code": {
                "type": "string",
                "enum": ["too_dark", "blurry", "weak_moment", "poor_subject", "other"],
            },
        },
        "required": ["feedback", "decision", "query", "files"],
        "additionalProperties": False,
    }

    def __init__(self, *, owner: str, tenant: str = "default") -> None:
        self._owner = owner
        self._tenant = tenant or "default"

    def run(self, args: dict[str, Any]) -> SkillResult:
        feedback = str(args.get("feedback") or "").strip()
        query = str(args.get("query") or "").strip()
        decision = str(args.get("decision") or "").strip().lower()
        files = [str(value) for value in (args.get("files") or []) if str(value).strip()]
        if not feedback or not query or decision not in {"accepted", "rejected"} or not files:
            return SkillResult(
                ok=False,
                error="'feedback', 'query', non-empty 'files', and valid 'decision' are required",
            )
        reason = str(args.get("reason_code") or "").strip() or infer_feedback_reason(feedback)
        conn = store.store_connect()
        try:
            experience_id = store.add_selection_experience(
                conn,
                owner=self._owner,
                tenant=self._tenant,
                selection_id=str(args.get("selection_id") or ""),
                query=query,
                subject=str(args.get("subject") or ""),
                style=str(args.get("style") or ""),
                platform=str(args.get("platform") or ""),
                decision=decision,
                reason_code=reason,
                feedback=feedback,
                files=files,
            )
        finally:
            conn.close()
        return SkillResult(
            ok=True,
            output=f"已记录选片反馈：{decision} / {reason}。",
            metadata={
                "experience_id": experience_id,
                "decision": decision,
                "reason_code": reason,
                "target_files": files,
                "ui_action": "feedback_recorded",
            },
        )


class RetrieveSelectionExperienceSkill:
    name = "retrieve_selection_experience"
    description = (
        "Retrieve similar past selection decisions and feedback for the current owner. "
        "Use before an open-ended personalized selection when prior taste may matter."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "subject": {"type": "string"},
            "style": {"type": "string"},
            "platform": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 10},
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    def __init__(self, *, owner: str, tenant: str = "default") -> None:
        self._owner = owner
        self._tenant = tenant or "default"

    def run(self, args: dict[str, Any]) -> SkillResult:
        query = str(args.get("query") or "").strip()
        if not query:
            return SkillResult(ok=False, error="'query' must be a non-empty string")
        try:
            limit = max(1, min(10, int(args.get("limit") or 5)))
        except (TypeError, ValueError):
            limit = 5
        conn = store.store_connect()
        try:
            experiences = store.list_selection_experiences(
                conn,
                owner=self._owner,
                tenant=self._tenant,
                limit=300,
            )
        finally:
            conn.close()
        rows: list[dict[str, Any]] = []
        for item in experiences:
            searchable = " ".join(
                [
                    item.get("query", ""),
                    item.get("subject", ""),
                    item.get("style", ""),
                    item.get("platform", ""),
                    item.get("feedback", ""),
                    item.get("reason_code", ""),
                ]
            )
            rows.append(
                {
                    **item,
                    "chunk_id": str(item["id"]),
                    "text": searchable,
                    "tokens": tokenize(searchable),
                }
            )
        enriched_query = " ".join(
            str(args.get(key) or "") for key in ("query", "subject", "style", "platform")
        )
        scores = bm25_scores(enriched_query, rows)
        ranked = sorted(
            (
                (float(scores.get(str(row["id"]), 0.0)), row)
                for row in rows
                if float(scores.get(str(row["id"]), 0.0)) > 0
            ),
            key=lambda item: (item[0], float(item[1].get("created_at") or 0.0)),
            reverse=True,
        )
        hits = [
            {
                "experience_id": row["id"],
                "query": row["query"],
                "subject": row["subject"],
                "style": row["style"],
                "platform": row["platform"],
                "decision": row["decision"],
                "reason_code": row["reason_code"],
                "feedback": row["feedback"],
                "files": row["files"],
                "score": round(score, 4),
            }
            for score, row in ranked[:limit]
        ]
        return SkillResult(
            ok=True,
            output=f"检索到 {len(hits)} 条相似的历史选片经验。",
            metadata={
                "experiences": hits,
                "count": len(hits),
                "retrieval": "experience_bm25",
                "owner": self._owner,
                "tenant": self._tenant,
                "ui_action": "experience_retrieved",
            },
        )


def register_experience_skills(
    registry: SkillRegistry,
    *,
    owner: str,
    tenant: str = "default",
) -> None:
    registry.register(RecordSelectionFeedbackSkill(owner=owner, tenant=tenant))
    registry.register(RetrieveSelectionExperienceSkill(owner=owner, tenant=tenant))
