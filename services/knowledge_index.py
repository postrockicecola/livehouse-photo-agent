"""Local enterprise-style knowledge index with chunking, ACL metadata, and BM25."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import yaml

_SUPPORTED_SUFFIXES = {".md", ".txt"}
_INDEX_DIR = ".rag_index"
_MANIFEST = "chunks.jsonl"
_META = "meta.json"
_WORD_RE = re.compile(r"[a-z0-9_]+|[\u4e00-\u9fff]", re.IGNORECASE)


def tokenize(text: str) -> list[str]:
    """Tokenize English words plus Chinese unigrams/bigrams without external NLP."""
    raw = _WORD_RE.findall((text or "").lower())
    tokens = list(raw)
    chinese = [token for token in raw if len(token) == 1 and "\u4e00" <= token <= "\u9fff"]
    tokens.extend(a + b for a, b in zip(chinese, chinese[1:]))
    return tokens


def _frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, text
    try:
        meta = yaml.safe_load(text[4:end]) or {}
    except yaml.YAMLError:
        return {}, text
    return (meta if isinstance(meta, dict) else {}), text[end + 5 :]


def _chunks(text: str, *, max_chars: int = 900, overlap_chars: int = 120) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    out: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(current) + len(paragraph) + 2 <= max_chars:
            current = f"{current}\n\n{paragraph}".strip()
            continue
        if current:
            out.append(current)
        if len(paragraph) <= max_chars:
            prefix = current[-overlap_chars:] if current else ""
            current = f"{prefix}\n\n{paragraph}".strip()
            continue
        start = 0
        while start < len(paragraph):
            out.append(paragraph[start : start + max_chars])
            start += max(1, max_chars - overlap_chars)
        current = ""
    if current:
        out.append(current)
    return out


def _source_rows(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not root.is_dir():
        return rows
    for path in sorted(root.rglob("*")):
        if (
            not path.is_file()
            or path.suffix.lower() not in _SUPPORTED_SUFFIXES
            or _INDEX_DIR in path.parts
            or any(part.startswith(".") for part in path.relative_to(root).parts)
        ):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        rows.append(
            {
                "source": str(path.relative_to(root)),
                "path": str(path.resolve()),
                "mtime_ns": stat.st_mtime_ns,
                "size": stat.st_size,
            }
        )
    return rows


def _signature(sources: list[dict[str, Any]]) -> str:
    raw = json.dumps(sources, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _allowed_owners(meta: dict[str, Any]) -> list[str]:
    raw = meta.get("allowed_owners", ["*"])
    if isinstance(raw, str):
        raw = [raw]
    return [str(owner) for owner in raw if str(owner).strip()] if isinstance(raw, list) else ["*"]


def build_knowledge_index(
    knowledge_dir: str | Path,
    *,
    embed_text: bool = False,
) -> dict[str, Any]:
    """Chunk source documents and optionally persist CLIP text vectors."""
    root = Path(knowledge_dir).expanduser().resolve()
    index_dir = root / _INDEX_DIR
    vectors_dir = index_dir / "vectors"
    index_dir.mkdir(parents=True, exist_ok=True)
    if embed_text:
        vectors_dir.mkdir(parents=True, exist_ok=True)
    sources = _source_rows(root)
    records: list[dict[str, Any]] = []
    embedder = None
    if embed_text:
        from services.embedding_service import EmbeddingService

        embedder = EmbeddingService

    for source in sources:
        path = Path(source["path"])
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        doc_meta, body = _frontmatter(raw)
        title = str(doc_meta.get("title") or path.stem)
        tenant = str(doc_meta.get("tenant") or "default")
        owners = _allowed_owners(doc_meta)
        for index, text in enumerate(_chunks(body)):
            chunk_id = hashlib.sha256(
                f"{source['source']}:{index}:{text}".encode("utf-8")
            ).hexdigest()[:20]
            vector_path = vectors_dir / f"{chunk_id}.npy"
            if not vector_path.is_file() and embedder is not None:
                vector = embedder.embed_text(text)
                if vector is not None:
                    tmp = vector_path.with_suffix(".tmp.npy")
                    np.save(tmp, vector)
                    os.replace(tmp, vector_path)
            records.append(
                {
                    "chunk_id": chunk_id,
                    "source": source["source"],
                    "title": title,
                    "tenant": tenant,
                    "allowed_owners": owners,
                    "source_url": str(doc_meta.get("source_url") or ""),
                    "chunk_index": index,
                    "text": text,
                    "tokens": tokenize(text),
                    "vector_path": (
                        str(vector_path.relative_to(index_dir))
                        if vector_path.is_file()
                        else ""
                    ),
                }
            )

    manifest = index_dir / _MANIFEST
    tmp_manifest = manifest.with_suffix(".tmp")
    with tmp_manifest.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    os.replace(tmp_manifest, manifest)
    meta = {
        "schema_version": "knowledge_index.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_signature": _signature(sources),
        "document_count": len(sources),
        "chunk_count": len(records),
        "vector_count": sum(1 for row in records if row.get("vector_path")),
    }
    meta_path = index_dir / _META
    tmp_meta = meta_path.with_suffix(".tmp")
    tmp_meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_meta, meta_path)
    return meta


def load_knowledge_index(
    knowledge_dir: str | Path,
    *,
    refresh: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    root = Path(knowledge_dir).expanduser().resolve()
    index_dir = root / _INDEX_DIR
    manifest = index_dir / _MANIFEST
    meta_path = index_dir / _META
    sources = _source_rows(root)
    meta: dict[str, Any] = {}
    if meta_path.is_file():
        try:
            loaded = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                meta = loaded
        except (OSError, json.JSONDecodeError):
            meta = {}
    if refresh or not manifest.is_file() or meta.get("source_signature") != _signature(sources):
        meta = build_knowledge_index(root, embed_text=False)
    records: list[dict[str, Any]] = []
    if manifest.is_file():
        try:
            for line in manifest.read_text(encoding="utf-8").splitlines():
                row = json.loads(line)
                if isinstance(row, dict):
                    records.append(row)
        except (OSError, json.JSONDecodeError):
            records = []
    return records, meta


def acl_allows(row: dict[str, Any], *, owner: str, tenant: str) -> bool:
    if str(row.get("tenant") or "default") != str(tenant or "default"):
        return False
    owners = {str(value) for value in (row.get("allowed_owners") or ["*"])}
    return "*" in owners or owner in owners


def bm25_scores(query: str, rows: list[dict[str, Any]]) -> dict[str, float]:
    """Small-corpus BM25 used as the deterministic lexical half of hybrid RAG."""
    query_tokens = tokenize(query)
    if not query_tokens or not rows:
        return {}
    docs = [list(row.get("tokens") or tokenize(str(row.get("text") or ""))) for row in rows]
    avg_len = sum(len(doc) for doc in docs) / max(1, len(docs))
    doc_freq = Counter(token for doc in docs for token in set(doc))
    scores: dict[str, float] = {}
    k1 = 1.5
    b = 0.75
    for row, doc in zip(rows, docs):
        counts = Counter(doc)
        score = 0.0
        for token in query_tokens:
            tf = counts.get(token, 0)
            if tf <= 0:
                continue
            idf = math.log(1.0 + (len(docs) - doc_freq[token] + 0.5) / (doc_freq[token] + 0.5))
            denom = tf + k1 * (1.0 - b + b * len(doc) / max(1.0, avg_len))
            score += idf * (tf * (k1 + 1.0)) / denom
        scores[str(row.get("chunk_id") or "")] = score
    return scores
