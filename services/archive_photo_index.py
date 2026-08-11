"""Local cross-session photo manifest and optional CLIP vector index."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from services.agent.skills.gallery_common import _caption, _load_rows, _text_blob
from utils.studio_sessions import scan_archive_session_dirs

_INDEX_DIR = Path("runtime") / "archive_index"
_MANIFEST_NAME = "manifest.jsonl"
_META_NAME = "meta.json"
_PREVIEW_FOLDERS = (
    "AI_Best_90+",
    "AI_Keep_60-90",
    "AI_Trash_Below60",
    "best",
    "keep",
    "trash",
)


def archive_index_dir(archive_root: str | Path) -> Path:
    return Path(archive_root).expanduser().resolve() / _INDEX_DIR


def _source_rows(archive_root: Path, *, max_sessions: int) -> list[dict[str, Any]]:
    sessions = scan_archive_session_dirs(archive_root)[: max(1, int(max_sessions))]
    out: list[dict[str, Any]] = []
    for session in sessions:
        previews = Path(str(session.get("previews_dir") or ""))
        analysis = previews / "analysis_results.json"
        if not previews.is_dir() or not analysis.is_file():
            continue
        try:
            stat = analysis.stat()
        except OSError:
            continue
        out.append(
            {
                "session_key": str(session.get("session_key") or previews.parent.name),
                "previews_dir": str(previews.resolve()),
                "analysis_path": str(analysis.resolve()),
                "mtime_ns": stat.st_mtime_ns,
                "size": stat.st_size,
            }
        )
    return out


def _source_signature(sources: list[dict[str, Any]]) -> str:
    payload = json.dumps(sources, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _resolve_image_path(previews_dir: Path, row: dict[str, Any]) -> Path | None:
    for key in ("path", "preview_path", "file_path"):
        raw = str(row.get(key) or "").strip()
        if not raw:
            continue
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = previews_dir / path
        if path.is_file():
            return path.resolve()
    file_name = str(row.get("file") or "").strip()
    if not file_name:
        return None
    direct = previews_dir / file_name
    if direct.is_file():
        return direct.resolve()
    for folder in _PREVIEW_FOLDERS:
        candidate = previews_dir / folder / file_name
        if candidate.is_file():
            return candidate.resolve()
    return None


def _vector_name(image_path: str) -> str:
    digest = hashlib.sha256(image_path.encode("utf-8")).hexdigest()
    return f"{digest}.npy"


def build_archive_photo_index(
    archive_root: str | Path,
    *,
    max_sessions: int = 80,
    embed_images: bool = False,
) -> dict[str, Any]:
    """Build an atomic archive manifest; optionally materialize CLIP image vectors."""
    root = Path(archive_root).expanduser().resolve()
    index_dir = archive_index_dir(root)
    vectors_dir = index_dir / "vectors"
    index_dir.mkdir(parents=True, exist_ok=True)
    if embed_images:
        vectors_dir.mkdir(parents=True, exist_ok=True)

    sources = _source_rows(root, max_sessions=max_sessions)
    records: list[dict[str, Any]] = []
    embedded = 0
    embedding_service = None
    if embed_images:
        from services.embedding_service import EmbeddingService

        embedding_service = EmbeddingService

    for source in sources:
        previews = Path(source["previews_dir"])
        session_key = str(source["session_key"])
        for row in _load_rows(str(previews)):
            source_file = str(row.get("file") or "").strip()
            if not source_file:
                continue
            image_path = _resolve_image_path(previews, row)
            vector_path = vectors_dir / _vector_name(str(image_path)) if image_path else None
            if vector_path is not None and not vector_path.is_file() and embedding_service is not None:
                vector = embedding_service.embed_image(image_path)
                if vector is not None:
                    tmp = vector_path.with_suffix(".tmp.npy")
                    np.save(tmp, vector)
                    os.replace(tmp, vector_path)
                    embedded += 1
            archive_id = f"{session_key}__{source_file}"
            records.append(
                {
                    "archive_id": archive_id,
                    "session_key": session_key,
                    "previews_dir": str(previews),
                    "source_file": source_file,
                    "image_path": str(image_path) if image_path else "",
                    "vector_path": (
                        str(vector_path.relative_to(index_dir))
                        if vector_path is not None and vector_path.is_file()
                        else ""
                    ),
                    "text": _text_blob(row),
                    "caption": _caption(row),
                    "tags": [str(tag) for tag in (row.get("tags") or [])],
                    "mood_tags": [str(tag) for tag in (row.get("mood_tags") or [])],
                    "overall_score": row.get("overall_score"),
                    "category": row.get("category"),
                    "dimensions": dict(row.get("dimensions") or {}),
                }
            )

    manifest = index_dir / _MANIFEST_NAME
    tmp_manifest = manifest.with_suffix(".tmp")
    with tmp_manifest.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    os.replace(tmp_manifest, manifest)

    meta = {
        "schema_version": "archive_photo_index.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "archive_root": str(root),
        "source_signature": _source_signature(sources),
        "session_count": len(sources),
        "row_count": len(records),
        "vector_count": sum(1 for row in records if row.get("vector_path")),
        "new_vectors": embedded,
        "model": "ViT-B-32/openai",
    }
    meta_path = index_dir / _META_NAME
    tmp_meta = meta_path.with_suffix(".tmp")
    tmp_meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_meta, meta_path)
    return meta


def load_archive_photo_index(
    archive_root: str | Path,
    *,
    max_sessions: int = 80,
    refresh: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Load the manifest, rebuilding metadata when source analysis files changed."""
    root = Path(archive_root).expanduser().resolve()
    index_dir = archive_index_dir(root)
    manifest = index_dir / _MANIFEST_NAME
    meta_path = index_dir / _META_NAME
    sources = _source_rows(root, max_sessions=max_sessions)
    signature = _source_signature(sources)
    meta: dict[str, Any] = {}
    if meta_path.is_file():
        try:
            raw_meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(raw_meta, dict):
                meta = raw_meta
        except (OSError, json.JSONDecodeError):
            meta = {}
    if refresh or not manifest.is_file() or meta.get("source_signature") != signature:
        meta = build_archive_photo_index(root, max_sessions=max_sessions, embed_images=False)

    records: list[dict[str, Any]] = []
    if manifest.is_file():
        try:
            for line in manifest.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                if isinstance(row, dict):
                    records.append(row)
        except (OSError, json.JSONDecodeError):
            records = []
    return records, meta
