"""CLIP-based visual embedding service for photo similarity search.

Architecture
------------
- Model:  OpenAI ``ViT-B-32`` via ``open-clip-torch`` (512-D L2-normalised float32).
- Storage: ``photo_embeddings`` SQLite table (BLOB); keyed by ``(photo_id, model_name)``.
  ``photos.vector_ref`` is updated to the model name so the schema comment stays accurate.
- Retrieval: brute-force numpy cosine similarity (corpus @ query).  Adequate up to ~50k
  photos at sub-10 ms; a vector-DB sidecar (sqlite-vec / Faiss) can be slotted in later.
- Graceful degradation: if ``open-clip-torch`` or ``torch`` are not installed the service
  logs a single warning and all public methods return ``None`` / empty results.

Typical call sequence
---------------------
1. After analyze job succeeds, call ``index_session(conn, session_id)`` (or trigger via
   ``POST /api/gallery/embeddings/index``) to batch-generate embeddings for the session.
2. At query time, ``find_similar_to_path(conn, image_path, top_k=10)`` returns the
   nearest neighbours across all indexed photos (or within a given session).
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    import sqlite3

logger = logging.getLogger(__name__)

_CLIP_MODEL_NAME = "ViT-B-32"
_CLIP_PRETRAINED = "openai"
_EMBED_DIM = 512


class EmbeddingService:
    """Process-level singleton for CLIP embedding generation and similarity search."""

    _model: Any = None
    _preprocess: Any = None
    _device: str = "cpu"
    _available: bool | None = None  # None = not yet probed
    _lock = threading.Lock()

    # ------------------------------------------------------------------
    # Model lifecycle
    # ------------------------------------------------------------------

    @classmethod
    def _load(cls) -> bool:
        """Lazy-load CLIP model on first use.  Thread-safe.  Returns True if ready."""
        if cls._available is True:
            return True
        if cls._available is False:
            return False
        with cls._lock:
            if cls._available is not None:
                return cls._available is True
            try:
                import open_clip  # type: ignore[import]
                import torch

                device = "cuda" if torch.cuda.is_available() else "cpu"
                # openai weights were trained with QuickGELU; force it to match, else newer
                # open-clip warns and silently uses plain GELU (degraded embeddings).
                model, _, preprocess = open_clip.create_model_and_transforms(
                    _CLIP_MODEL_NAME, pretrained=_CLIP_PRETRAINED, force_quick_gelu=True
                )
                model = model.eval().to(device)
                cls._model = model
                cls._preprocess = preprocess
                cls._device = device
                cls._available = True
                logger.info(
                    "EmbeddingService: loaded %s/%s on %s",
                    _CLIP_MODEL_NAME,
                    _CLIP_PRETRAINED,
                    device,
                )
                return True
            except ImportError:
                logger.warning(
                    "EmbeddingService unavailable — install open-clip-torch: "
                    "pip install open-clip-torch"
                )
                cls._available = False
                return False
            except Exception as exc:
                logger.error("EmbeddingService: model load failed: %s", exc)
                cls._available = False
                return False

    @classmethod
    def is_available(cls) -> bool:
        return cls._load()

    # ------------------------------------------------------------------
    # Embedding generation
    # ------------------------------------------------------------------

    @classmethod
    def embed_image(cls, image_path: str | Path) -> np.ndarray | None:
        """Encode *image_path* to a 512-D L2-normalised float32 CLIP embedding.

        Returns ``None`` when the service is unavailable or the image cannot be read.
        """
        if not cls._load():
            return None
        try:
            import torch
            from PIL import Image

            img_tensor = (
                cls._preprocess(Image.open(image_path).convert("RGB"))
                .unsqueeze(0)
                .to(cls._device)
            )
            with torch.no_grad():
                features = cls._model.encode_image(img_tensor)
                features = features / features.norm(dim=-1, keepdim=True)
            return features.squeeze(0).cpu().numpy().astype(np.float32)
        except Exception as exc:
            logger.warning("embed_image failed for %s: %s", image_path, exc)
            return None

    @classmethod
    def embed_batch(cls, image_paths: list[str | Path]) -> list[np.ndarray | None]:
        """Embed a list of images.  Returns one entry per input (None on failure)."""
        return [cls.embed_image(p) for p in image_paths]

    # ------------------------------------------------------------------
    # Similarity maths
    # ------------------------------------------------------------------

    @staticmethod
    def cosine_similarity(
        query: np.ndarray, corpus: np.ndarray
    ) -> np.ndarray:
        """Return cosine similarities of *query* (D,) against *corpus* (N, D).

        Both vectors must already be L2-normalised (unit norm) — as returned by
        ``embed_image`` — so the dot product equals cosine similarity directly.
        """
        return corpus @ query  # shape (N,)

    # ------------------------------------------------------------------
    # DB-backed similarity search
    # ------------------------------------------------------------------

    @classmethod
    def find_similar_to_path(
        cls,
        conn: "sqlite3.Connection",
        image_path: str | Path,
        *,
        top_k: int = 10,
        session_id: int | None = None,
        exclude_self: bool = True,
    ) -> list[dict[str, Any]]:
        """Return top-k visually similar photos from the embedding index.

        Args:
            conn:        Open luma_brain DB connection.
            image_path:  Source image (will be embedded on the fly if needed).
            top_k:       Maximum results to return.
            session_id:  Restrict corpus to one session; ``None`` = search all.
            exclude_self: Skip results whose ``file_path`` matches *image_path*.

        Returns:
            List of dicts ``{photo_id, file_path, similarity, file_name}``
            sorted by descending similarity.  Empty list when service is unavailable
            or no embeddings are indexed.
        """
        query_emb = cls.embed_image(image_path)
        if query_emb is None:
            return []

        rows = _load_embeddings(conn, session_id=session_id)
        if not rows:
            return []

        corpus = np.stack([r["embedding"] for r in rows])  # (N, D)
        scores = cls.cosine_similarity(query_emb, corpus)  # (N,)
        order = np.argsort(scores)[::-1]

        abs_query = str(Path(image_path).resolve())
        results: list[dict[str, Any]] = []
        for idx in order:
            if len(results) >= top_k:
                break
            row = rows[int(idx)]
            if exclude_self and str(Path(row["file_path"]).resolve()) == abs_query:
                continue
            results.append(
                {
                    "photo_id": row["photo_id"],
                    "file_path": row["file_path"],
                    "file_name": Path(row["file_path"]).name,
                    "similarity": float(scores[idx]),
                }
            )
        return results

    @classmethod
    def index_session(
        cls,
        conn: "sqlite3.Connection",
        session_id: int,
        *,
        force_reindex: bool = False,
        batch_size: int = 16,
    ) -> dict[str, Any]:
        """Generate and persist CLIP embeddings for all ANALYZED photos in *session_id*.

        Skips photos that already have an embedding (unless ``force_reindex``).
        Returns a summary dict ``{indexed, skipped, failed, elapsed_ms}``.
        """
        if not cls._load():
            return {"ok": False, "error": "open-clip-torch not installed"}

        rows = conn.execute(
            """
            SELECT p.id, p.file_path
            FROM photos p
            WHERE p.session_id = ? AND p.status = 'ANALYZED'
            ORDER BY p.id
            """,
            (session_id,),
        ).fetchall()

        already_indexed: set[int] = set()
        if not force_reindex:
            existing = conn.execute(
                "SELECT photo_id FROM photo_embeddings WHERE model_name = ?",
                (_CLIP_MODEL_NAME,),
            ).fetchall()
            already_indexed = {r[0] for r in existing}

        pending = [
            (r["id"], r["file_path"])
            for r in rows
            if force_reindex or r["id"] not in already_indexed
        ]

        indexed = skipped = failed = 0
        skipped = len(rows) - len(pending)
        t0 = time.monotonic()

        for i in range(0, len(pending), batch_size):
            batch = pending[i : i + batch_size]
            for photo_id, file_path in batch:
                emb = cls.embed_image(file_path)
                if emb is None:
                    failed += 1
                    continue
                try:
                    _upsert_embedding(conn, photo_id, emb, _CLIP_MODEL_NAME)
                    indexed += 1
                except Exception as exc:
                    logger.warning("embed upsert failed photo_id=%s: %s", photo_id, exc)
                    failed += 1

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        logger.info(
            "index_session session=%s: indexed=%s skipped=%s failed=%s elapsed=%dms",
            session_id,
            indexed,
            skipped,
            failed,
            elapsed_ms,
        )
        return {
            "ok": True,
            "session_id": session_id,
            "model": _CLIP_MODEL_NAME,
            "indexed": indexed,
            "skipped": skipped,
            "failed": failed,
            "total": len(rows),
            "elapsed_ms": elapsed_ms,
        }


# ------------------------------------------------------------------
# DB helpers (package-private; called by gallery_routes and luma_brain)
# ------------------------------------------------------------------


def _upsert_embedding(
    conn: "sqlite3.Connection",
    photo_id: int,
    vector: np.ndarray,
    model_name: str,
) -> None:
    """Persist *vector* for *photo_id*; updates ``photos.vector_ref``."""
    blob = vector.astype(np.float32).tobytes()
    conn.execute(
        """
        INSERT INTO photo_embeddings (photo_id, model_name, vector, dim)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(photo_id, model_name) DO UPDATE SET
          vector = excluded.vector,
          dim    = excluded.dim,
          created_at = strftime('%s', 'now')
        """,
        (photo_id, model_name, blob, int(vector.shape[0])),
    )
    conn.execute(
        "UPDATE photos SET vector_ref = ? WHERE id = ?",
        (model_name, photo_id),
    )
    conn.commit()


def _load_embeddings(
    conn: "sqlite3.Connection",
    session_id: int | None = None,
) -> list[dict[str, Any]]:
    """Load all embeddings (optionally scoped to one session) as a list of dicts."""
    if session_id is not None:
        rows = conn.execute(
            """
            SELECT p.id AS photo_id, p.file_path, pe.vector
            FROM photo_embeddings pe
            JOIN photos p ON pe.photo_id = p.id
            WHERE p.session_id = ? AND pe.model_name = ?
            ORDER BY p.id
            """,
            (session_id, _CLIP_MODEL_NAME),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT p.id AS photo_id, p.file_path, pe.vector
            FROM photo_embeddings pe
            JOIN photos p ON pe.photo_id = p.id
            WHERE pe.model_name = ?
            ORDER BY p.id
            """,
            (_CLIP_MODEL_NAME,),
        ).fetchall()

    return [
        {
            "photo_id": r["photo_id"],
            "file_path": r["file_path"],
            "embedding": np.frombuffer(r["vector"], dtype=np.float32),
        }
        for r in rows
    ]
