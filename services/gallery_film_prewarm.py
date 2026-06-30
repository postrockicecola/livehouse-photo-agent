"""Background Cinestill cache warmup for homepage gallery (``film_render_cache``)."""
from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from services.film_render_service import (
    path_allowed_for_film_render,
    render_film_to_cache,
    resolve_film_sources_for_export,
)
from services.path_service import PathResolver
from services.result_service import load_results

logger = logging.getLogger(__name__)

_prewarm_last_enqueued: dict[str, float] = {}
_prewarm_lock = threading.Lock()

GALLERY_CINESTILL_VARIANT = "film_cinestill_800t"
# Keep in sync with ``web/lib/galleryDisplayUrl.ts`` ``GALLERY_FILM_THUMB_MAX_SIDE``.
GALLERY_CINESTILL_MAX_SIDE = 720
# Match ``api/gallery_routes.get_film_render`` for non-special variants.
GALLERY_CINESTILL_CACHE_EXTRA = "displayReady1"


def _env_truthy(name: str, default: str = "1") -> bool:
    return os.getenv(name, default).strip().lower() not in {"0", "false", "no", "off"}


def previews_base_from_artifacts(
    analysis_results_path: str | Path | None,
    source_dir: str | Path | None = None,
) -> str | None:
    if analysis_results_path:
        p = Path(analysis_results_path).expanduser()
        if p.is_file():
            return str(p.parent.resolve())
    if source_dir:
        sd = Path(source_dir).expanduser()
        candidates = [sd, sd / "Previews", sd / "previews"]
        for cand in candidates:
            json_path = cand / "analysis_results.json"
            if json_path.is_file():
                return str(cand.resolve())
    return None


def _film_source_path(resolver: PathResolver, entry: dict[str, Any]) -> Path | None:
    name = str(entry.get("file") or "").strip()
    if name:
        sources = resolve_film_sources_for_export(resolver, name)
        if sources:
            return sources[0][0]
    for key in ("path", "before_path"):
        raw = entry.get(key)
        if not raw or not isinstance(raw, str):
            continue
        p = Path(raw).expanduser()
        if p.is_file():
            return p.resolve()
    return None


def _prewarm_one(
    *,
    cache_root: Path,
    resolver: PathResolver,
    entry: dict[str, Any],
) -> str:
    src = _film_source_path(resolver, entry)
    if src is None:
        return "skip_no_source"
    if not path_allowed_for_film_render(src, resolver):
        return "skip_not_allowed"
    rotate = int(entry.get("rotate_degrees") or 0)
    try:
        render_film_to_cache(
            src_path=src,
            variant_id=GALLERY_CINESTILL_VARIANT,
            rotate=rotate,
            max_side=GALLERY_CINESTILL_MAX_SIDE,
            cache_root=cache_root,
            cache_key_extra=GALLERY_CINESTILL_CACHE_EXTRA,
        )
        return "ok"
    except Exception:
        logger.warning("prewarm failed for %s", entry.get("file") or src, exc_info=True)
        return "error"


def run_gallery_cinestill_prewarm(previews_base_dir: str) -> dict[str, Any]:
    """Render Cinestill thumbs for all gallery rows (sorted by score desc)."""
    base = Path(previews_base_dir).expanduser().resolve()
    if not base.is_dir():
        return {"ok": False, "error": "previews_base_not_found", "previews_base": str(base)}

    max_images = int(os.getenv("LUMA_GALLERY_FILM_PREWARM_MAX", "0") or "0")
    default_workers = min(4, max(2, (os.cpu_count() or 4) // 2))
    workers = max(1, min(4, int(os.getenv("LUMA_GALLERY_FILM_PREWARM_WORKERS", str(default_workers)) or str(default_workers))))
    priority_first = max(0, int(os.getenv("LUMA_GALLERY_FILM_PREWARM_PRIORITY_FIRST", "40") or "40"))

    rows = load_results(str(base))
    rows.sort(key=lambda r: float(r.get("overall_score") or 0), reverse=True)
    if max_images > 0:
        rows = rows[:max_images]

    from utils.runtime_paths import runtime_dir

    cache_root = runtime_dir(base, create=True) / "film_render_cache"
    resolver = PathResolver(base)

    stats: dict[str, int] = {"ok": 0, "skip_no_source": 0, "skip_not_allowed": 0, "error": 0}
    total = len(rows)
    if total == 0:
        return {
            "ok": True,
            "previews_base": str(base),
            "total": 0,
            "workers": workers,
            "stats": stats,
        }

    logger.info(
        "gallery cinestill prewarm start: base=%s total=%s workers=%s max_side=%s priority_first=%s",
        base,
        total,
        workers,
        GALLERY_CINESTILL_MAX_SIDE,
        priority_first,
    )

    def _run_batch(batch: list[dict[str, Any]]) -> None:
        if not batch:
            return
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [
                pool.submit(
                    _prewarm_one,
                    cache_root=cache_root,
                    resolver=resolver,
                    entry=entry,
                )
                for entry in batch
            ]
            for fut in as_completed(futures):
                try:
                    tag = fut.result()
                except Exception:
                    tag = "error"
                stats[tag] = stats.get(tag, 0) + 1

    if priority_first > 0 and total > priority_first:
        _run_batch(rows[:priority_first])
        _run_batch(rows[priority_first:])
    else:
        _run_batch(rows)

    logger.info("gallery cinestill prewarm done: base=%s stats=%s", base, stats)
    return {
        "ok": True,
        "previews_base": str(base),
        "total": total,
        "workers": workers,
        "max_side": GALLERY_CINESTILL_MAX_SIDE,
        "stats": stats,
    }


def _prewarm_cooldown_sec() -> int:
    return max(60, int(os.getenv("LUMA_GALLERY_FILM_PREWARM_COOLDOWN_SEC", "900") or "900"))


def _send_prewarm_task(base: str) -> str | None:
    try:
        from celery_app import celery_app

        async_result = celery_app.send_task("tasks.prewarm_gallery_cinestill", args=[base])
        return str(async_result.id)
    except Exception:
        logger.warning("enqueue gallery cinestill prewarm failed", exc_info=True)
        return None


def enqueue_gallery_cinestill_prewarm(
    *,
    analysis_results_path: str | Path | None = None,
    source_dir: str | Path | None = None,
    force: bool = False,
) -> str | None:
    """Fire-and-forget Celery task; returns task id or None if disabled / no base."""
    if not _env_truthy("LUMA_GALLERY_FILM_PREWARM", "1"):
        return None
    base = previews_base_from_artifacts(analysis_results_path, source_dir)
    if not base:
        return None
    if not force:
        now = time.time()
        cooldown = _prewarm_cooldown_sec()
        with _prewarm_lock:
            last = _prewarm_last_enqueued.get(base, 0.0)
            if now - last < cooldown:
                logger.debug("gallery cinestill prewarm skipped (cooldown) base=%s", base)
                return None
            _prewarm_last_enqueued[base] = now
    else:
        with _prewarm_lock:
            _prewarm_last_enqueued[base] = time.time()
    return _send_prewarm_task(base)


def try_enqueue_gallery_cinestill_prewarm(
    *,
    analysis_results_path: str | Path | None = None,
    source_dir: str | Path | None = None,
) -> str | None:
    """Like ``enqueue_gallery_cinestill_prewarm`` but respects per-gallery cooldown (e.g. page refresh)."""
    return enqueue_gallery_cinestill_prewarm(
        analysis_results_path=analysis_results_path,
        source_dir=source_dir,
        force=False,
    )
