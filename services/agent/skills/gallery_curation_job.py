"""Gallery chat skills for async curation jobs (submit / poll / cancel)."""
from __future__ import annotations

from typing import Any

from services.agent.curation_jobs import (
    cancel_curation_job,
    compile_job_goal,
    get_curation_job,
    schedule_curation_job,
    submit_curation_job,
)
from services.agent.skills.base import SkillResult


def _owner_from_args(args: dict[str, Any]) -> str:
    return str(args.get("owner") or "").strip() or "anon:default"


def _session_from_args(args: dict[str, Any]) -> str:
    return str(args.get("session_id") or "").strip() or "default"


def _job_output(public: dict[str, Any]) -> str:
    status = str(public.get("status") or "unknown")
    job_id = str(public.get("job_id") or "")
    if status == "queued":
        extra = "（与进行中的相同交片任务合并）" if public.get("deduped") else ""
        return f"已提交交片任务 {job_id}，状态 queued{extra}。完成后可 poll_curation_job。"
    if status == "running":
        return f"交片任务 {job_id} 正在运行。"
    if status == "done":
        count = int(public.get("count") or 0)
        return f"交片任务 {job_id} 已完成，选出 {count} 张。"
    if status == "cancelled":
        return f"交片任务 {job_id} 已取消，未作为成功交片。"
    if status == "failed":
        err = public.get("error") or "failed"
        return f"交片任务 {job_id} 失败：{err}"
    return f"交片任务 {job_id} 状态 {status}。"


class SubmitCurationJobSkill:
    name = "submit_curation_job"
    description = (
        "Submit a long-running delivery/curation job (这场交片 / 交片给客户). "
        "Returns a job_id immediately. Do not treat queued/running as a finished selection. "
        "Use poll_curation_job later; use cancel_curation_job to abort."
    )
    parameters = {
        "type": "object",
        "properties": {
            "user_text": {
                "type": "string",
                "description": "Original user request, e.g. 这场交30张给客户，偏鼓手",
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            "query": {
                "type": "string",
                "description": "Optional subject residue (鼓手 / 吉他手).",
            },
        },
        "additionalProperties": False,
    }

    def __init__(self, base_dir: str) -> None:
        self._base_dir = base_dir

    def run(self, args: dict[str, Any]) -> SkillResult:
        base_dir = str(self._base_dir or "").strip()
        if not base_dir:
            return SkillResult(ok=False, error="gallery base_dir is not configured")
        user_text = str(args.get("user_text") or "").strip()
        query = str(args.get("query") or "").strip() or None
        limit = args.get("limit")
        try:
            limit_n = int(limit) if limit is not None else None
        except (TypeError, ValueError):
            return SkillResult(ok=False, error="limit must be an integer")
        if not user_text and not query and limit_n is None:
            return SkillResult(ok=False, error="user_text or query/limit is required")
        goal_args = args.get("goal_args")
        if not isinstance(goal_args, dict):
            goal_args = compile_job_goal(user_text, limit=limit_n, query=query)
        timeout = args.get("timeout_sec")
        try:
            timeout_sec = float(timeout) if timeout is not None else 180.0
        except (TypeError, ValueError):
            timeout_sec = 180.0
        public = submit_curation_job(
            owner=_owner_from_args(args),
            session_id=_session_from_args(args),
            base_dir=base_dir,
            goal_args=goal_args,
            user_text=user_text,
            timeout_sec=timeout_sec,
        )
        if not public.get("ok"):
            return SkillResult(ok=False, error=str(public.get("error") or "submit failed"))
        backend = "defer"
        if not public.get("deduped"):
            backend = schedule_curation_job(str(public["job_id"]))
        meta = {
            "job_id": public["job_id"],
            "status": public["status"],
            "deduped": bool(public.get("deduped")),
            "backend": backend,
            "count": 0,
        }
        return SkillResult(ok=True, output=_job_output(public), metadata=meta)


class PollCurationJobSkill:
    name = "poll_curation_job"
    description = (
        "Poll an async curation job by job_id. "
        "Only status=done means the selection succeeded. cancelled/failed are not success."
    )
    parameters = {
        "type": "object",
        "properties": {
            "job_id": {"type": "string", "description": "Id returned by submit_curation_job."},
        },
        "required": ["job_id"],
        "additionalProperties": False,
    }

    def __init__(self, base_dir: str) -> None:
        self._base_dir = base_dir

    def run(self, args: dict[str, Any]) -> SkillResult:
        job_id = str(args.get("job_id") or "").strip()
        if not job_id:
            return SkillResult(ok=False, error="job_id is required")
        public = get_curation_job(job_id, owner=_owner_from_args(args))
        if public is None:
            return SkillResult(ok=False, error=f"unknown job: {job_id}")
        files = list(public.get("files") or [])
        status = str(public.get("status") or "")
        meta: dict[str, Any] = {
            "job_id": public["job_id"],
            "status": status,
            "count": int(public.get("count") or len(files)),
        }
        if status == "done" and files:
            meta["files"] = files
            meta["selected_keys"] = files
            meta["ui_action"] = "reload_curation"
        if status == "cancelled":
            meta["success"] = False
        return SkillResult(
            ok=True,
            output=_job_output(public),
            metadata=meta,
        )


class CancelCurationJobSkill:
    name = "cancel_curation_job"
    description = (
        "Cancel a queued or running curation job. "
        "A cancelled job must not be reported as a successful delivery."
    )
    parameters = {
        "type": "object",
        "properties": {
            "job_id": {"type": "string"},
        },
        "required": ["job_id"],
        "additionalProperties": False,
    }

    def __init__(self, base_dir: str) -> None:
        self._base_dir = base_dir

    def run(self, args: dict[str, Any]) -> SkillResult:
        job_id = str(args.get("job_id") or "").strip()
        if not job_id:
            return SkillResult(ok=False, error="job_id is required")
        public = cancel_curation_job(job_id, owner=_owner_from_args(args))
        if not public.get("ok") and public.get("status") != "cancelled":
            return SkillResult(
                ok=False,
                error=str(public.get("error") or "cancel failed"),
                metadata={
                    "job_id": job_id,
                    "status": public.get("status"),
                    "success": False,
                },
            )
        return SkillResult(
            ok=True,
            output=_job_output(public),
            metadata={
                "job_id": public.get("job_id") or job_id,
                "status": public.get("status") or "cancelled",
                "success": False,
            },
        )
