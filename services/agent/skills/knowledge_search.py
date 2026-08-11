"""ACL-aware enterprise knowledge retrieval skill."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from services.agent.skills.base import SkillResult
from services.knowledge_index import (
    acl_allows,
    bm25_scores,
    load_knowledge_index,
)


class KnowledgeSearchSkill:
    name = "knowledge_search"
    description = (
        "Retrieve grounded chunks from internal photography/platform knowledge documents. "
        "Use for policies, platform specifications, workflow manuals, and internal guidance. "
        "Results are filtered by the caller's owner and tenant ACL."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 12},
            "sources": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional source path/title substrings.",
            },
            "mode": {
                "type": "string",
                "enum": ["text", "vector", "hybrid"],
                "description": "hybrid uses available prebuilt text vectors plus BM25.",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        knowledge_dir: str,
        *,
        owner: str,
        tenant: str = "default",
    ) -> None:
        self._knowledge_dir = str(knowledge_dir)
        self._owner = str(owner)
        self._tenant = str(tenant or "default")

    def run(self, args: dict[str, Any]) -> SkillResult:
        query = str(args.get("query") or "").strip()
        if not query:
            return SkillResult(ok=False, error="'query' must be a non-empty string")
        try:
            limit = max(1, min(12, int(args.get("limit") or 5)))
        except (TypeError, ValueError):
            limit = 5
        mode = str(args.get("mode") or "hybrid").strip().lower()
        if mode not in {"text", "vector", "hybrid"}:
            mode = "hybrid"
        source_filters = [
            str(value).strip().lower()
            for value in (args.get("sources") or [])
            if str(value).strip()
        ]
        rows, index_meta = load_knowledge_index(self._knowledge_dir)
        allowed = [
            row
            for row in rows
            if acl_allows(row, owner=self._owner, tenant=self._tenant)
            and (
                not source_filters
                or any(
                    needle in f"{row.get('source', '')} {row.get('title', '')}".lower()
                    for needle in source_filters
                )
            )
        ]
        lexical = bm25_scores(query, allowed)
        vectors = (
            self._vector_scores(query, allowed, index_dir=Path(self._knowledge_dir) / ".rag_index")
            if mode in {"vector", "hybrid"}
            else {}
        )
        max_lexical = max(lexical.values(), default=0.0)
        ranked: list[tuple[float, dict[str, Any], float, float]] = []
        for row in allowed:
            chunk_id = str(row.get("chunk_id") or "")
            text_score = float(lexical.get(chunk_id, 0.0))
            vector_score = float(vectors.get(chunk_id, 0.0))
            if mode == "text" and text_score <= 0:
                continue
            if mode == "vector" and not vectors:
                continue
            if mode == "vector" and vector_score <= 0:
                continue
            if mode == "hybrid" and text_score <= 0 and vector_score <= 0:
                continue
            normalized_text = text_score / max_lexical if max_lexical > 0 else 0.0
            score = normalized_text * 0.55 + max(0.0, vector_score) * 0.45
            ranked.append((score, row, text_score, vector_score))
        ranked.sort(key=lambda item: item[0], reverse=True)

        chunks: list[dict[str, Any]] = []
        sources: list[dict[str, Any]] = []
        for score, row, text_score, vector_score in ranked[:limit]:
            source_ref = f"{row.get('source')}#chunk-{row.get('chunk_index')}"
            chunks.append(
                {
                    "source_ref": source_ref,
                    "title": row.get("title"),
                    "text": row.get("text"),
                    "score": round(score, 4),
                    "bm25": round(text_score, 4),
                    "vector_similarity": round(vector_score, 4),
                }
            )
            sources.append(
                {
                    "source_ref": source_ref,
                    "source": row.get("source"),
                    "source_url": row.get("source_url"),
                    "title": row.get("title"),
                }
            )
        retrieval = (
            "knowledge_hybrid"
            if mode == "hybrid" and vectors
            else ("knowledge_vector" if mode == "vector" and vectors else "knowledge_bm25")
        )
        summary = (
            f"从 {int(index_meta.get('document_count') or 0)} 份知识文档中检索，"
            f"当前权限可访问 {len(allowed)} 个分块，返回 {len(chunks)} 个证据块。"
        )
        if mode in {"vector", "hybrid"} and not vectors:
            summary += " 文本向量尚未构建，本次使用 BM25。"
        return SkillResult(
            ok=True,
            output=summary,
            metadata={
                "chunks": chunks,
                "sources": sources,
                "count": len(chunks),
                "retrieval": retrieval,
                "owner": self._owner,
                "tenant": self._tenant,
                "ui_action": "knowledge_sources",
            },
        )

    @staticmethod
    def _vector_scores(
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
            try:
                vector = np.load(index_dir / rel, allow_pickle=False).astype(np.float32)
            except (OSError, ValueError):
                continue
            vector_rows.append((str(row.get("chunk_id") or ""), vector))
        if not vector_rows:
            return {}
        from services.embedding_service import EmbeddingService

        query_vector = EmbeddingService.embed_text(query)
        if query_vector is None:
            return {}
        corpus = np.stack([vector for _, vector in vector_rows])
        similarities = EmbeddingService.cosine_similarity(query_vector, corpus)
        return {
            chunk_id: float(similarities[index])
            for index, (chunk_id, _) in enumerate(vector_rows)
        }
