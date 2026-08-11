"""Cross-session multimodal retrieval over the local photography archive."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from services.agent.skills.base import SkillResult
from services.agent.skills.gallery_common import (
    _SEMANTIC_MIN_SIM,
    _expand_query_terms,
    _query_hit_score,
)
from services.archive_photo_index import archive_index_dir, load_archive_photo_index
from utils.runtime_session import resolve_archive_root_for_runtime


class ArchiveSearchSkill:
    name = "archive_search"
    description = (
        "Search analyzed photos across archived shooting sessions, not only the active Gallery. "
        "Use for 其它场次/历史照片/整个档案/去年拍的 requests. Hybrid mode combines "
        "tag/caption text hits with prebuilt CLIP image vectors and returns grounded archive IDs."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Cross-session semantic query, e.g. 蓝色逆光下的吉他手.",
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 30},
            "session_hint": {
                "type": "string",
                "description": "Optional date, venue, artist, or session-folder substring.",
            },
            "exclude_current_session": {
                "type": "boolean",
                "description": "Exclude the active Gallery session (default false).",
            },
            "mode": {
                "type": "string",
                "enum": ["text", "clip", "hybrid"],
                "description": "hybrid uses available prebuilt CLIP vectors and text (default).",
            },
            "min_score": {
                "type": "number",
                "description": "Optional minimum overall quality score.",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    def __init__(self, base_dir: str) -> None:
        self._base_dir = str(base_dir)

    def run(self, args: dict[str, Any]) -> SkillResult:
        query = str(args.get("query") or "").strip()
        if not query:
            return SkillResult(ok=False, error="'query' must be a non-empty string")
        try:
            limit = max(1, min(30, int(args.get("limit") or 10)))
        except (TypeError, ValueError):
            limit = 10
        mode = str(args.get("mode") or "hybrid").strip().lower()
        if mode not in {"text", "clip", "hybrid"}:
            mode = "hybrid"
        session_hint = str(args.get("session_hint") or "").strip().lower()
        exclude_current = bool(args.get("exclude_current_session"))
        min_score = args.get("min_score")

        archive_root = resolve_archive_root_for_runtime(base_dir=self._base_dir)
        records, index_meta = load_archive_photo_index(archive_root)
        active = Path(self._base_dir).expanduser().resolve()
        filtered: list[dict[str, Any]] = []
        for row in records:
            if session_hint and session_hint not in str(row.get("session_key") or "").lower():
                continue
            if exclude_current:
                try:
                    if Path(str(row.get("previews_dir") or "")).resolve() == active:
                        continue
                except OSError:
                    pass
            if min_score is not None:
                try:
                    if float(row.get("overall_score") or 0.0) < float(min_score):
                        continue
                except (TypeError, ValueError):
                    continue
            filtered.append(row)

        terms = _expand_query_terms(query)
        text_scores = {
            str(row.get("archive_id") or ""): _query_hit_score(
                str(row.get("text") or "").lower(),
                terms,
            )
            for row in filtered
        }
        clip_scores: dict[str, float] = {}
        clip_attempted = mode in {"clip", "hybrid"}
        if clip_attempted:
            clip_scores = self._clip_scores(
                query,
                filtered,
                index_dir=archive_index_dir(archive_root),
            )

        candidates: list[dict[str, Any]] = []
        for row in filtered:
            archive_id = str(row.get("archive_id") or "")
            text_score = float(text_scores.get(archive_id, 0))
            clip_score = float(clip_scores.get(archive_id, 0.0))
            text_hit = text_score > 0
            clip_hit = clip_score >= _SEMANTIC_MIN_SIM
            if mode == "text" and not text_hit:
                continue
            if mode == "clip" and not clip_hit:
                continue
            if mode == "hybrid" and not (text_hit or clip_hit):
                continue
            overall = float(row.get("overall_score") or 0.0)
            rank_score = (
                (1.25 if text_hit else 0.0)
                + min(text_score, 24.0) / 24.0
                + clip_score * 2.0
                + overall / 200.0
            )
            candidates.append(
                {
                    **row,
                    "_rank_score": rank_score,
                    "_text_score": text_score,
                    "_clip_score": clip_score,
                }
            )
        candidates.sort(
            key=lambda row: (
                float(row.get("_rank_score") or 0.0),
                float(row.get("overall_score") or 0.0),
            ),
            reverse=True,
        )

        top_rows: list[dict[str, Any]] = []
        citations: list[dict[str, Any]] = []
        for row in candidates[:limit]:
            archive_id = str(row.get("archive_id") or "")
            why_parts = []
            if float(row.get("_text_score") or 0.0) > 0:
                why_parts.append("标签/caption 命中")
            if float(row.get("_clip_score") or 0.0) > 0:
                why_parts.append(f"CLIP {float(row['_clip_score']):.2f}")
            why_parts.append(f"overall {float(row.get('overall_score') or 0.0):.0f}")
            compact = {
                "file": archive_id,
                "source_file": row.get("source_file"),
                "session_key": row.get("session_key"),
                "previews_dir": row.get("previews_dir"),
                "overall_score": row.get("overall_score"),
                "category": row.get("category"),
                "tags": row.get("tags") or [],
                "caption": row.get("caption") or "",
                "why": " · ".join(why_parts),
            }
            top_rows.append(compact)
            citations.append(
                {
                    "file": archive_id,
                    "source_file": row.get("source_file"),
                    "session_key": row.get("session_key"),
                    "previews_dir": row.get("previews_dir"),
                    "image_path": row.get("image_path"),
                }
            )

        files = [str(row["file"]) for row in top_rows]
        vector_count = int(index_meta.get("vector_count") or 0)
        retrieval = (
            "archive_hybrid"
            if mode == "hybrid" and clip_scores
            else ("archive_clip" if mode == "clip" and clip_scores else "archive_text")
        )
        summary = (
            f"跨 {int(index_meta.get('session_count') or 0)} 个场次检索 "
            f"{int(index_meta.get('row_count') or len(records))} 张照片，"
            f"找到 {len(candidates)} 张候选，返回 {len(files)} 张。"
        )
        if clip_attempted and vector_count == 0:
            summary += " CLIP 向量索引尚未构建，本次使用文本检索。"
        return SkillResult(
            ok=True,
            output=summary,
            metadata={
                "rows": top_rows,
                "files": files,
                "citations": citations,
                "count": len(candidates),
                "query": query,
                "retrieval": retrieval,
                "ui_action": "archive_search",
                "archive_root": str(archive_root),
                "indexed_corpus_size": int(index_meta.get("row_count") or len(records)),
                "vector_count": vector_count,
            },
        )

    @staticmethod
    def _clip_scores(
        query: str,
        rows: list[dict[str, Any]],
        *,
        index_dir: Path,
    ) -> dict[str, float]:
        vector_rows: list[tuple[str, np.ndarray]] = []
        for row in rows:
            rel = str(row.get("vector_path") or "").strip()
            if not rel:
                continue
            path = index_dir / rel
            try:
                vector = np.load(path, allow_pickle=False).astype(np.float32)
            except (OSError, ValueError):
                continue
            vector_rows.append((str(row.get("archive_id") or ""), vector))
        if not vector_rows:
            return {}
        from services.embedding_service import EmbeddingService

        query_vector = EmbeddingService.embed_text(query)
        if query_vector is None:
            return {}
        corpus = np.stack([vector for _, vector in vector_rows])
        scores = EmbeddingService.cosine_similarity(query_vector, corpus)
        return {
            archive_id: float(scores[index])
            for index, (archive_id, _) in enumerate(vector_rows)
        }
