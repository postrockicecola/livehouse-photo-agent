"""Gallery API routes and related service glue code.

**Main path:** REST for ``analysis_results.json``, ``/image``, export, ``/api/gallery/results``,
``POST /api/tasks/analyze`` (creates ``jobs`` row + ``tasks.run_job``), ``POST /api/ingest/check_new_images``
(SD hook → ``tasks.process_brain_ingested``).

**Also registered (legacy):** ``tasks.run_image_analysis`` remains on workers for broker compatibility;
prefer ``/api/tasks/analyze`` or ``create_analyze_path_job`` + ``send_task("tasks.run_job", ...)``.
"""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from celery import Celery
from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from services.gallery_film_prewarm import (
    enqueue_gallery_cinestill_prewarm,
    try_enqueue_gallery_cinestill_prewarm,
)
from services.film_render_service import (
    AUTOMATED_VARIANT_ID,
    EXPORT_DIR_GRADED_FROM_RAW,
    EXPORT_DIR_JPEG,
    EXPORT_DIR_RAW_COPY,
    FILM_VARIANT_IDS,
    is_raw_path,
    path_allowed_for_film_render,
    render_film_to_cache,
    resolve_film_catalog_paths,
    resolve_film_sources_for_export,
)
from services.edit_adjustments import EditAdjustments, parse_edit_adjustments_response
from services.image_service import ImageService
from services.path_service import PathResolver
from services.result_service import (
    load_gallery_page,
    read_orientation_degrees_from_raw,
)
from utils.logging_context import new_trace_id
from utils.luma_brain import brain_connect, create_analyze_path_job, create_curate_path_job

router = APIRouter()

# 批量导出 JPEG 默认套用的胶片型号（预览 / alternate 等非显式胶片分支）。
_DEFAULT_EXPORT_JPEG_FILM = "film_cinestill_800t"

SERVER_BUILD = "orientation-debug-v2"
BASE_DIR = os.getcwd()
_CELERY_BROKER = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
_CELERY_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")
celery_client = Celery("livehouse_api", broker=_CELERY_BROKER, backend=_CELERY_BACKEND)

# ``_runtime_base_dir`` is hot; cache until session ref or results JSON mtimes change.
_gallery_active_dir_cache: tuple[tuple[str, float, float, float], str] | None = None


def configure_gallery_routes(base_dir: str) -> None:
    global BASE_DIR, _gallery_active_dir_cache
    BASE_DIR = base_dir
    _gallery_active_dir_cache = None


class ExportImageSpec(BaseModel):
    """One row for ``/api/export-images``. ``file`` is the catalog basename (RAW / preview lookup)."""

    model_config = ConfigDict(extra="ignore")

    file: str
    rotate: int = 0
    film_variant: str | None = None
    film_source_path_quoted: str | None = None
    alternate_jpeg_path_quoted: str | None = None
    # When ``film_variant == film_automated``: per-image VLM grade params.
    automated_adjust: dict[str, float] | None = None

    @field_validator("rotate", mode="before")
    @classmethod
    def _coerce_rotate(cls, v):  # noqa: ANN001
        if v is None:
            return 0
        return v


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    images: list[str] = Field(default_factory=list)
    items: list[ExportImageSpec] | None = None
    category: str = "unknown"
    # When true, images without an explicit ``film_variant`` use persisted session vibe.
    use_session_vibe: bool = False


class VibeResolveRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    prompt: str = ""


class VibeSessionPutRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    prompt: str = ""
    clear: bool = False


class CurationFeedbackEntryModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    verdict: str
    like_reasons: list[str] = Field(default_factory=list)
    reject_reasons: list[str] = Field(default_factory=list)
    note: str = ""


class GalleryCurationPutRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    selected_keys: list[str] = Field(default_factory=list)
    feedback_by_key: dict[str, CurationFeedbackEntryModel] | dict[str, dict] = Field(default_factory=dict)
    export_by_file: dict[str, ExportImageSpec] | dict[str, dict] = Field(default_factory=dict)
    clear: bool = False


class PairwisePreferenceEntryModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    winner_key: str
    loser_key: str
    group_id: str | None = None
    reason_tags: list[str] = Field(default_factory=list)
    created_unix: int | None = None
    source: str = "unknown"


class PairwisePreferencesPostRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    entries: list[PairwisePreferenceEntryModel] = Field(default_factory=list)
    replace_same_pair_in_group: bool = False
    clear: bool = False


def _curation_api_payload(data: dict[str, Any] | None) -> dict[str, Any] | None:
    from utils.gallery_curation import normalize_gallery_curation

    if not data:
        return None
    norm = normalize_gallery_curation(data)
    return {
        "version": norm.get("version"),
        "selected_keys": list(norm.get("selected_keys") or []),
        "feedback_by_key": dict(norm.get("feedback_by_key") or {}),
        "export_by_file": dict(norm.get("export_by_file") or {}),
        "updated_unix": norm.get("updated_unix"),
    }


def _export_processing_opts() -> dict[str, int | bool]:
    """Batch export film options from ``processing`` + env ``LIVEHOUSE_EXPORT_FILM_FROM_RAW``."""
    from utils.config_loader import ConfigLoader

    proc = ConfigLoader.load().get("processing") or {}
    if not isinstance(proc, dict):
        proc = {}
    env = os.getenv("LIVEHOUSE_EXPORT_FILM_FROM_RAW", "").strip().lower()
    if env in ("0", "false", "no", "off"):
        from_raw = False
    elif env in ("1", "true", "yes", "on"):
        from_raw = True
    else:
        from_raw = bool(proc.get("export_film_from_raw", True))
    jpeg_ms = int(proc.get("export_film_jpeg_max_side", 3200) or 3200)
    raw_ms = int(proc.get("export_film_raw_max_side", jpeg_ms) or jpeg_ms)
    return {
        "export_film_from_raw": from_raw,
        "export_film_jpeg_max_side": max(256, min(4096, jpeg_ms)),
        "export_film_raw_max_side": max(256, min(4096, raw_ms)),
    }


def _export_specs_list(req: ExportRequest) -> list[ExportImageSpec]:
    """Prefer structured ``items``; fall back to legacy ``images`` basename list."""
    if req.items is not None and len(req.items) > 0:
        return req.items
    if req.images:
        return [ExportImageSpec(file=f) for f in req.images]
    return []


@lru_cache(maxsize=8)
def _path_resolver(base_dir: str) -> PathResolver:
    return PathResolver(Path(base_dir))


def _mtime(path: str) -> float:
    try:
        return float(os.path.getmtime(path))
    except OSError:
        return -1.0


def _analysis_results_has_entries(dir_path: str) -> bool:
    """Cheap true if ``analysis_results.json`` looks like a non-empty JSON array (first 64 KiB)."""
    p = os.path.join(dir_path, "analysis_results.json")
    if not os.path.isfile(p):
        return False
    try:
        if os.path.getsize(p) < 4:
            return False
        with open(p, "r", encoding="utf-8") as f:
            head = f.read(65536).lstrip()
    except OSError:
        return False
    if not head.startswith("["):
        return True
    inner = head[1:].lstrip()
    if not inner:
        return False
    return not inner.startswith("]")


def _runtime_base_dir() -> str:
    """Resolve active Previews directory for JSON + ``/image``.

    **Override:** set env ``LIVEHOUSE_GALLERY_PREVIEWS_DIR`` to an absolute Previews path to
    force Lab/API to that folder (ignores ``latest_session.json`` logic below).

    Otherwise: prefer ``latest_session.json`` → ``previews_dir`` when it has gallery rows.
    If that pointer is stale/empty while ``gallery_server`` was started with a populated
    ``BASE_DIR``, fall back to ``BASE_DIR``.
    """
    global _gallery_active_dir_cache
    override = (os.environ.get("LIVEHOUSE_GALLERY_PREVIEWS_DIR") or "").strip()
    if override:
        forced = str(Path(override).expanduser().resolve())
        if os.path.isdir(forced):
            return forced

    base = str(Path(BASE_DIR).expanduser().resolve())
    try:
        from utils.runtime_session import read_newest_latest_session_pointer

        hit = read_newest_latest_session_pointer(base_dir=base)
        if hit is not None:
            refp, ref = hit
            cand = str(Path(ref.get("previews_dir", "")).expanduser().resolve())
            if cand and os.path.isdir(cand):
                cand_json = os.path.join(cand, "analysis_results.json")
                base_json = os.path.join(base, "analysis_results.json")
                cache_key = (
                    str(refp.resolve()),
                    _mtime(str(refp)),
                    _mtime(cand_json),
                    _mtime(base_json),
                )
                if _gallery_active_dir_cache and _gallery_active_dir_cache[0] == cache_key:
                    return _gallery_active_dir_cache[1]

                cand_ok = _analysis_results_has_entries(cand)
                base_ok = _analysis_results_has_entries(base)
                if cand_ok:
                    resolved = cand
                elif base_ok:
                    resolved = base
                else:
                    resolved = cand

                _gallery_active_dir_cache = (cache_key, resolved)
                return resolved
    except Exception:
        pass
    return base


def _gallery_path_roots() -> list[Path]:
    """Directories used to resolve relative ``path`` values (JSON often relative to project / session)."""
    roots: list[Path] = []
    seen: set[str] = set()

    def add(p: Path) -> None:
        try:
            r = p.resolve(strict=False)
        except OSError:
            return
        k = str(r)
        if k not in seen:
            seen.add(k)
            roots.append(r)

    add(Path(BASE_DIR))
    add(Path(_runtime_base_dir()))
    return roots


def _resolve_gallery_image_path(path_query: str) -> Path | None:
    """Resolve ``/image`` / film-render ``path=`` to a real file (relative to gallery & runtime roots)."""
    raw = (path_query or "").strip()
    if not raw:
        return None
    try:
        first = Path(raw).expanduser()
    except OSError:
        return None

    trials: list[Path] = []
    if first.is_absolute():
        trials.append(first.resolve())

    for base in _gallery_path_roots():
        trials.append((base / raw).resolve())
        trials.append((base / raw.lstrip("./")).resolve())

    seen: set[str] = set()
    for cand in trials:
        try:
            key = str(cand)
            if key in seen:
                continue
            seen.add(key)
            if cand.is_file():
                return cand.resolve()
        except OSError:
            continue

    try:
        ap = Path(os.path.abspath(raw))
        if ap.is_file():
            return ap.resolve()
    except OSError:
        pass
    return None


_lab_url = os.getenv("LIVEHOUSE_LAB_URL", "http://127.0.0.1:3000").rstrip("/")


@router.get("/analysis_results.json")
def serve_analysis_results_json():
    """Expose ``analysis_results.json`` for static poll-based clients (same dir as runtime gallery)."""
    path = os.path.join(_runtime_base_dir(), "analysis_results.json")
    if not os.path.isfile(path):
        return JSONResponse(content=[])
    headers = {"Cache-Control": "no-store"}
    return FileResponse(path, media_type="application/json", headers=headers)


@router.get("/", response_class=HTMLResponse)
def index() -> str:
    """Minimal landing page; browse UI lives on Next Lab (override with LIVEHOUSE_LAB_URL)."""
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Livehouse Gallery API</title>
  <style>
    body {{ font-family: system-ui,sans-serif; background:#111; color:#e5e5e5; padding:2rem; max-width:36rem; line-height:1.5; }}
    a {{ color:#6ee7b7; }}
    .muted {{ color:#a3a3a3; font-size:.9rem; margin-top:1.25rem; }}
  </style>
</head>
<body>
  <p>相册浏览请使用 Next <strong>Live Lab</strong>：<a href="{_lab_url}/">{_lab_url}/</a></p>
  <p class="muted">本端口提供 REST API 与图片服务 · <a href="/docs">OpenAPI /docs</a> · <a href="/healthz">healthz</a></p>
  <p class="muted">分类静态页仍可通过 <code>/best/gallery.html</code> 等路径访问（若已生成）。</p>
</body>
</html>
"""


@lru_cache(maxsize=8192)
def _auto_capture_rotation(path_abs: str) -> int:
    """Capture rotation (deg) for an EXIF-stripped preview, cached per absolute path.

    Orientation is immutable per file, so caching is safe and keeps ``/image`` cheap even
    on cache hits. Returns 0 for EXIF-carrying JPEGs (``exif_transpose`` handles those).
    """
    from services.jpeg_exif_orientation import resolve_capture_rotation_degrees

    try:
        return int(resolve_capture_rotation_degrees(path_abs))
    except Exception:  # noqa: BLE001 - orientation is best-effort; never block image serving
        return 0


@router.get("/image")
def get_image(
    path: str = Query(...),
    rotate: int = Query(0),
    max_side: int = Query(1200, ge=256, le=4096),
    auto_orient: int = Query(
        1,
        description="When 1 and no explicit rotate is given, auto-upright EXIF-stripped previews "
        "from the RAW sibling's orientation. Pass 0 to serve as-is.",
    ),
):
    img_path_resolved = _resolve_gallery_image_path(path)
    if img_path_resolved is None:
        raise HTTPException(status_code=404, detail="image not found")
    img_path_abs = str(img_path_resolved)
    # Centralized orientation: if the caller didn't pass a rotation, resolve the capture
    # rotation here so *every* consumer (gallery, agent panels, RLHF, …) gets upright pixels
    # without each call site needing to know about EXIF/RAW orientation.
    eff_rotate = rotate
    if rotate == 0 and auto_orient:
        eff_rotate = _auto_capture_rotation(img_path_abs)
    image_service = ImageService(Path(_runtime_base_dir()))
    cached = image_service.build_cached_image(Path(img_path_abs), rotate=eff_rotate, max_side=max_side)
    if cached and cached.exists():
        return FileResponse(
            str(cached),
            media_type="image/jpeg",
            headers=ImageService.CACHE_HEADERS,
        )
    # Cache write can fail (permissions / full disk). Still apply EXIF + rotate like cache path,
    # otherwise raw FileResponse ignores ``rotate`` and browsers may disagree with EXIF → wrong orientation.
    blob = image_service.encode_display_thumbnail(Path(img_path_abs), rotate=eff_rotate, max_side=max_side)
    if blob is not None:
        return Response(
            content=blob,
            media_type="image/jpeg",
            headers={"Cache-Control": "public, max-age=120"},
        )
    return FileResponse(img_path_abs, media_type="image/jpeg")


@router.get("/api/lab/film-render")
def get_film_render(
    path: str = Query(..., description="Absolute path to source image (JPEG preview or RAW)"),
    variant: str = Query(..., description="film_livehouse | film_cinestill_800t | …"),
    rotate: int = Query(0),
    max_side: int = Query(2200, ge=256, le=4096),
    optical: str | None = Query(
        None,
        description='Optional JSON optical overrides, e.g. {"flow":22,"wear":12,"flow_angle":-15}',
    ),
    adjust: str | None = Query(
        None,
        description='Automated grade params (variant=film_automated), e.g. {"exposure":0.7,"shadows":20}',
    ),
):
    """Run ``op_kernel`` film kernel on demand (cached under ``Previews/runtime/film_render_cache``)."""
    from services.optical_params import parse_optical_p1

    base_dir = _runtime_base_dir()
    resolver = _path_resolver(base_dir)
    src_resolved = _resolve_gallery_image_path(path)
    if src_resolved is None:
        raise HTTPException(status_code=404, detail="image not found") from None
    src_abs = src_resolved
    if variant not in FILM_VARIANT_IDS and variant != AUTOMATED_VARIANT_ID:
        raise HTTPException(status_code=400, detail="unknown variant")
    if not path_allowed_for_film_render(src_abs, resolver):
        raise HTTPException(status_code=403, detail="path not allowed")

    adjustments: EditAdjustments | None = None
    if variant == AUTOMATED_VARIANT_ID and adjust:
        adjustments = parse_edit_adjustments_response(adjust)

    from services.optical_params import parse_optical_console

    optical_p1 = None
    if optical is not None:
        try:
            optical_p1 = parse_optical_console(optical)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    from utils.runtime_paths import runtime_dir

    cache_root = runtime_dir(base_dir, create=True) / "film_render_cache"
    # Bust disk cache when a variant's grade changes (same variant id, new look).
    if variant == "film_cold_v2":
        lab_film_cache_extra = "warmOrange7"
    elif variant == "film_cold_v4":
        lab_film_cache_extra = "cinemaClassic3"
    elif variant == "film_black_mist":
        lab_film_cache_extra = "blackMist2"
    elif variant == "film_ricoh_gr":
        lab_film_cache_extra = "ricohGrPositive1"
    elif variant == "film_livehouse":
        lab_film_cache_extra = "livehouseClarity6"
    elif variant == "film_wong_kar_wai":
        lab_film_cache_extra = "wongKarWai1"
    elif variant == "film_retro_literary_portrait":
        lab_film_cache_extra = "retroLiteraryPortrait1"
    elif variant == AUTOMATED_VARIANT_ID:
        lab_film_cache_extra = "automatedGradeV1"
    else:
        lab_film_cache_extra = "displayReady1"
    try:
        cached = render_film_to_cache(
            src_path=src_abs,
            variant_id=variant,
            rotate=rotate,
            max_side=max_side,
            cache_root=cache_root,
            cache_key_extra=lab_film_cache_extra,
            optical=optical_p1,
            adjustments=adjustments,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail="film render failed") from e

    return FileResponse(
        str(cached),
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=604800"},
    )


@router.get("/{folder_type}/gallery.html", response_class=HTMLResponse)
def serve_category_gallery(folder_type: str) -> str:
    allowed_folders = {"best", "keep", "trash", "AI_Best_90+", "AI_Keep_60-90", "AI_Trash_Below60"}
    if folder_type not in allowed_folders:
        raise HTTPException(status_code=404, detail="folder not found")
    gallery_file = os.path.join(_runtime_base_dir(), folder_type, "gallery.html")
    if not os.path.exists(gallery_file):
        raise HTTPException(status_code=404, detail="gallery file not found")
    try:
        with open(gallery_file, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/vibe/session")
def get_vibe_session():
    """Read persisted session vibe for the active Previews directory."""
    from utils.session_vibe import read_session_vibe

    base_dir = _runtime_base_dir()
    data = read_session_vibe(base_dir)
    return {"active": data is not None, "session_vibe": data, "previews_dir": base_dir}


@router.put("/api/vibe/session")
def put_vibe_session(req: VibeSessionPutRequest):
    """Resolve ``prompt`` → film variant and persist under ``Previews/runtime/session_vibe.json``."""
    from services.vibe_film_policy import resolve_vibe_from_prompt, session_vibe_payload_from_decision
    from utils.session_vibe import clear_session_vibe, read_session_vibe, write_session_vibe

    base_dir = _runtime_base_dir()
    global _gallery_active_dir_cache
    if req.clear:
        clear_session_vibe(base_dir)
        _gallery_active_dir_cache = None
        return {"active": False, "session_vibe": None, "previews_dir": base_dir}

    decision = resolve_vibe_from_prompt(req.prompt)
    payload = session_vibe_payload_from_decision(decision)
    written = write_session_vibe(base_dir, payload)
    if written is None:
        raise HTTPException(status_code=500, detail="无法写入 session_vibe.json")
    _gallery_active_dir_cache = None
    return {"active": True, "session_vibe": read_session_vibe(base_dir), "previews_dir": base_dir}


@router.post("/api/vibe/resolve")
def post_vibe_resolve(req: VibeResolveRequest):
    """Resolve prompt to film variant without persisting (for live preview in UI)."""
    from services.vibe_film_policy import resolve_vibe_from_prompt

    decision = resolve_vibe_from_prompt(req.prompt)
    return {"decision": decision.to_json()}


@router.get("/api/gallery/curation")
def get_gallery_curation():
    """Homepage selection + per-image export prefs (``Previews/runtime/gallery_curation.json``)."""
    from utils.gallery_curation import read_gallery_curation

    base_dir = _runtime_base_dir()
    data = read_gallery_curation(base_dir)
    if not data:
        return {"active": False, "curation": None, "previews_dir": base_dir}
    return {
        "active": True,
        "curation": _curation_api_payload(data),
        "previews_dir": base_dir,
    }


@router.put("/api/gallery/curation")
def put_gallery_curation(req: GalleryCurationPutRequest):
    from utils.gallery_curation import clear_gallery_curation, read_gallery_curation, write_gallery_curation

    base_dir = _runtime_base_dir()
    if req.clear:
        clear_gallery_curation(base_dir)
        return {"active": False, "curation": None, "previews_dir": base_dir}

    export_raw: dict[str, Any] = {}
    for k, v in (req.export_by_file or {}).items():
        if hasattr(v, "model_dump"):
            export_raw[str(k)] = v.model_dump(exclude_none=True)
        elif isinstance(v, dict):
            export_raw[str(k)] = v

    feedback_raw: dict[str, Any] = {}
    for k, v in (req.feedback_by_key or {}).items():
        if hasattr(v, "model_dump"):
            feedback_raw[str(k)] = v.model_dump(exclude_none=True)
        elif isinstance(v, dict):
            feedback_raw[str(k)] = v

    written = write_gallery_curation(
        base_dir,
        selected_keys=list(req.selected_keys or []),
        feedback_by_key=feedback_raw or None,
        export_by_file=export_raw,
    )
    if written is None:
        raise HTTPException(status_code=500, detail="无法写入 gallery_curation.json")
    taste_rebuild = None
    try:
        from services.taste_profile import rebuild_taste_profile

        taste_rebuild = rebuild_taste_profile(base_dir)
    except Exception:
        taste_rebuild = {"ok": False, "error": "rebuild_failed"}
    data = read_gallery_curation(base_dir)
    return {
        "active": True,
        "curation": _curation_api_payload(data),
        "previews_dir": base_dir,
        "taste_rebuild": taste_rebuild,
    }


@router.get("/api/gallery/taste")
def get_gallery_taste():
    from services.taste_profile import few_shot_prompt_block, read_taste_profile

    base_dir = _runtime_base_dir()
    profile = read_taste_profile(base_dir)
    return {
        "active": profile is not None,
        "profile": profile,
        "few_shot_preview": few_shot_prompt_block(profile) if profile else "",
        "previews_dir": base_dir,
    }


@router.post("/api/gallery/taste/rebuild")
def post_gallery_taste_rebuild():
    from services.taste_profile import rebuild_taste_profile

    base_dir = _runtime_base_dir()
    out = rebuild_taste_profile(base_dir)
    if not out.get("ok"):
        return JSONResponse(status_code=400, content=out)
    return out


def _pairwise_api_payload(data: dict[str, Any] | None) -> dict[str, Any] | None:
    if not data:
        return None
    return {
        "version": data.get("version"),
        "entries": list(data.get("entries") or []),
        "updated_unix": data.get("updated_unix"),
    }


@router.get("/api/gallery/pairwise-preferences")
def get_gallery_pairwise_preferences(
    group_id: str | None = Query(None, description="Filter edges to one burst/similarity group."),
    limit: int | None = Query(None, ge=1, le=500),
):
    from services.pairwise_preferences import pairwise_stats
    from utils.pairwise_preferences import list_pairwise_entries, read_pairwise_preferences

    base_dir = _runtime_base_dir()
    data = read_pairwise_preferences(base_dir)
    if not data:
        return {
            "active": False,
            "preferences": None,
            "stats": pairwise_stats(base_dir),
            "previews_dir": base_dir,
        }
    entries = list_pairwise_entries(base_dir, group_id=group_id, limit=limit)
    return {
        "active": True,
        "preferences": {
            "version": data.get("version"),
            "entries": entries,
            "updated_unix": data.get("updated_unix"),
        },
        "stats": pairwise_stats(base_dir),
        "previews_dir": base_dir,
    }


@router.post("/api/gallery/pairwise-preferences")
def post_gallery_pairwise_preferences(req: PairwisePreferencesPostRequest):
    from services.pairwise_preferences import pairwise_stats
    from utils.pairwise_preferences import (
        append_pairwise_preferences,
        clear_pairwise_preferences,
        read_pairwise_preferences,
    )

    base_dir = _runtime_base_dir()
    if req.clear:
        clear_pairwise_preferences(base_dir)
        return {
            "active": False,
            "preferences": None,
            "previews_dir": base_dir,
            "stats": pairwise_stats(base_dir),
        }
    raw_entries = [e.model_dump(exclude_none=True) for e in req.entries]
    if not raw_entries:
        raise HTTPException(status_code=400, detail="entries required unless clear=true")
    out = append_pairwise_preferences(
        base_dir,
        raw_entries,
        replace_same_pair_in_group=req.replace_same_pair_in_group,
    )
    if not out.get("ok"):
        raise HTTPException(status_code=500, detail=str(out.get("error") or "write_failed"))
    data = read_pairwise_preferences(base_dir)
    return {
        "active": True,
        "preferences": _pairwise_api_payload(data),
        "previews_dir": base_dir,
        "append": out,
        "stats": pairwise_stats(base_dir),
    }


@router.post("/api/export-images")
def export_images(req: ExportRequest):
    from utils.json_safe import json_safe

    try:
        return _export_images_impl(req)
    except Exception as e:
        import traceback

        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content=json_safe(
                {
                    "success": False,
                    "error": "export failed",
                    "detail": str(e),
                }
            ),
        )


def _export_images_impl(req: ExportRequest):
    specs = _export_specs_list(req)
    if not specs:
        return JSONResponse({"success": False, "error": "没有选择图片"}, status_code=400)
    base_dir = _runtime_base_dir()
    resolver = _path_resolver(base_dir)
    session_dir, raw_hint = resolver.session_and_raw_hint()
    export_opts = _export_processing_opts()
    from services.vibe_film_policy import effective_film_variant_for_export, session_vibe_is_matched
    from utils.session_vibe import read_session_vibe

    session_vibe = read_session_vibe(base_dir) if req.use_session_vibe else None
    # 与 Previews 同级：…/session/exported_images/export_*（不再放在 Previews 下）
    export_base = Path(base_dir).parent / "exported_images"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    export_root = export_base / f"export_{timestamp}"
    jpeg_dir = export_root / EXPORT_DIR_JPEG
    raw_out = export_root / EXPORT_DIR_RAW_COPY
    graded_dir = export_root / EXPORT_DIR_GRADED_FROM_RAW
    jpeg_dir.mkdir(parents=True, exist_ok=True)
    raw_out.mkdir(parents=True, exist_ok=True)
    if export_opts["export_film_from_raw"]:
        graded_dir.mkdir(parents=True, exist_ok=True)

    # 与 Lab 浏览器缓存隔离；cache_key_extra 避免命中旧版/错误缓存条目。
    from utils.runtime_paths import runtime_dir as previews_runtime_dir

    film_export_cache = previews_runtime_dir(base_dir, create=True) / "film_export_cache"
    _EXPORT_FILM_CACHE_TAG = "exportFilmV16"
    _EXPORT_RAW_CACHE_TAG = "exportFilmRawDevelopV1"

    def _render_film_to_jpeg(
        src: Path,
        dest_basename: str,
        variant_id: str,
        rotate_deg: int,
        *,
        dest_dir: Path,
        max_side: int,
        cache_tag: str,
        adjustments: EditAdjustments | None = None,
    ) -> str | None:
        """Return error message or None on success."""
        if not src.is_file():
            return "源图不是有效文件"
        if variant_id not in FILM_VARIANT_IDS and variant_id != AUTOMATED_VARIANT_ID:
            return f"未知胶片型号 {variant_id}"
        if not path_allowed_for_film_render(src, resolver):
            return "胶片源路径不允许"
        try:
            cached = render_film_to_cache(
                src_path=src,
                variant_id=variant_id,
                rotate=rotate_deg,
                max_side=int(max_side),
                cache_root=film_export_cache,
                cache_key_extra=cache_tag,
                adjustments=adjustments,
            )
            shutil.copy2(cached, dest_dir / dest_basename)
        except Exception as e:
            return str(e)
        return None

    success_jpeg = 0
    success_raw = 0
    success_graded_from_raw = 0
    errors = []
    export_feedback_items: list[dict[str, object]] = []
    for spec in specs:
        image_name = spec.file
        dest_jpeg_name = Path(PathResolver._strip_resource_fork_name(image_name)).name
        try:
            jpeg_done = False
            raw_done = False
            graded_done = False
            is_automated = (spec.film_variant or "").strip() == AUTOMATED_VARIANT_ID
            spec_adjustments: EditAdjustments | None = (
                EditAdjustments(**{k: float(v) for k, v in (spec.automated_adjust or {}).items()
                                   if k in EditAdjustments().as_dict()})
                if is_automated
                else None
            )
            if is_automated:
                variant_user = AUTOMATED_VARIANT_ID
            else:
                variant_user = effective_film_variant_for_export(
                    spec_film_variant=spec.film_variant,
                    session_vibe=session_vibe,
                    use_session_vibe=req.use_session_vibe,
                )
                if spec.film_variant and not variant_user and spec.film_variant not in (
                    FILM_VARIANT_IDS,
                    "session_vibe",
                ):
                    errors.append(f"{image_name}: 未知胶片型号 {spec.film_variant}")
                    variant_user = None
            variant_preview = variant_user or _DEFAULT_EXPORT_JPEG_FILM
            variant_graded = variant_user or _DEFAULT_EXPORT_JPEG_FILM

            pq = (spec.film_source_path_quoted or "").strip()
            explicit_src = _resolve_gallery_image_path(pq) if pq else None
            catalog = resolve_film_catalog_paths(
                resolver, image_name, explicit_source=explicit_src
            )

            attempts: list[tuple[Path, str, str]] = []
            if variant_user and explicit_src and explicit_src.is_file() and not is_raw_path(explicit_src):
                attempts.append((explicit_src, variant_user, "film_source"))
            if (spec.alternate_jpeg_path_quoted or "").strip():
                altp = _resolve_gallery_image_path(spec.alternate_jpeg_path_quoted.strip())
                if altp and altp.is_file() and not is_raw_path(altp):
                    attempts.append((altp, _DEFAULT_EXPORT_JPEG_FILM, "alternate"))
            for src_p, tag in resolve_film_sources_for_export(
                resolver, image_name, explicit_source=explicit_src
            ):
                vid = variant_user if tag == "film_source" and variant_user else variant_preview
                attempts.append((src_p, vid, tag))

            seen: set[str] = set()
            for src_p, vid, tag in attempts:
                key = str(src_p.resolve())
                if key in seen:
                    continue
                seen.add(key)
                err = _render_film_to_jpeg(
                    src_p,
                    dest_jpeg_name,
                    vid,
                    spec.rotate,
                    dest_dir=jpeg_dir,
                    max_side=int(export_opts["export_film_jpeg_max_side"]),
                    cache_tag=_EXPORT_FILM_CACHE_TAG,
                    adjustments=spec_adjustments if vid == AUTOMATED_VARIANT_ID else None,
                )
                if err is None:
                    success_jpeg += 1
                    jpeg_done = True
                    break
                errors.append(f"{image_name} [{tag}]: {err}")

            if not jpeg_done:
                if not attempts:
                    errors.append(f"{image_name}: 无可用于胶片导出的源图（无预览 / 无有效路径）")
                elif (
                    spec.film_variant
                    and spec.film_variant not in FILM_VARIANT_IDS
                    and spec.film_variant != "session_vibe"
                ):
                    errors.append(
                        f"{image_name}: 未知胶片型号 {spec.film_variant}，且所有候选源均未成功导出",
                    )
                else:
                    errors.append(f"{image_name}: 胶片导出失败（已尝试全部候选源）")

            if export_opts["export_film_from_raw"]:
                raw_film_src = catalog.get("raw")
                if raw_film_src and raw_film_src.is_file():
                    err_g = _render_film_to_jpeg(
                        raw_film_src,
                        dest_jpeg_name,
                        variant_graded,
                        spec.rotate,
                        dest_dir=graded_dir,
                        max_side=int(export_opts["export_film_raw_max_side"]),
                        cache_tag=_EXPORT_RAW_CACHE_TAG,
                        adjustments=spec_adjustments if variant_graded == AUTOMATED_VARIANT_ID else None,
                    )
                    if err_g is None:
                        success_graded_from_raw += 1
                        graded_done = True
                    else:
                        errors.append(f"{image_name} [graded_from_raw]: {err_g}")
                else:
                    stem = Path(PathResolver._strip_resource_fork_name(image_name)).stem
                    hint = f"raw_dir={raw_hint}" if raw_hint else "无 raw_dir 提示"
                    errors.append(
                        f"{image_name}: graded_from_raw 跳过（RAW 未找到 session={session_dir} {hint} stem={stem}）",
                    )

            raw_src = catalog.get("raw")
            if raw_src and raw_src.is_file():
                shutil.copy2(raw_src, raw_out / raw_src.name)
                success_raw += 1
                raw_done = True
            else:
                stem = Path(PathResolver._strip_resource_fork_name(image_name)).stem
                hint = f"raw_dir={raw_hint}" if raw_hint else "无 raw_dir 提示"
                errors.append(
                    f"{image_name}: RAW 未找到（session={session_dir} {hint} stem={stem}）"
                )

            if jpeg_done or raw_done or graded_done:
                eff = variant_user
                if not eff and req.use_session_vibe and session_vibe and session_vibe_is_matched(session_vibe):
                    eff = str(session_vibe.get("film_variant") or "").strip() or None
                row: dict[str, object] = {
                    "file": image_name,
                    "rotate": int(spec.rotate or 0),
                    "jpeg_exported": jpeg_done,
                    "raw_copied": raw_done,
                    "graded_from_raw": graded_done,
                }
                if spec.film_variant:
                    row["film_variant"] = str(spec.film_variant).strip()
                if eff:
                    row["film_variant_effective"] = eff
                if spec.film_source_path_quoted:
                    row["film_source_path_quoted"] = str(spec.film_source_path_quoted).strip()
                if spec.alternate_jpeg_path_quoted:
                    row["alternate_jpeg_path_quoted"] = str(
                        spec.alternate_jpeg_path_quoted
                    ).strip()
                if is_automated and spec_adjustments is not None:
                    row["automated_adjust"] = spec_adjustments.as_dict()
                export_feedback_items.append(row)
        except Exception as e:
            errors.append(f"{image_name}: {e}")
    ok = success_jpeg > 0 or success_raw > 0 or success_graded_from_raw > 0
    if ok and export_feedback_items:
        from utils.export_feedback import append_export_feedback_event

        sv_film: str | None = None
        if req.use_session_vibe and session_vibe and session_vibe_is_matched(session_vibe):
            sv_film = str(session_vibe.get("film_variant") or "").strip() or None
        try:
            append_export_feedback_event(
                base_dir,
                category=str(req.category or "unknown"),
                use_session_vibe=bool(req.use_session_vibe),
                session_vibe_film_variant=sv_film,
                export_path=str(export_root),
                items=export_feedback_items,
            )
        except Exception:
            pass
    resp: dict[str, object] = {
        "success": ok,
        "count": success_jpeg,
        "count_jpeg": success_jpeg,
        "count_raw": success_raw,
        "count_graded_from_raw": success_graded_from_raw,
        "export_film_from_raw": bool(export_opts["export_film_from_raw"]),
        "use_session_vibe": bool(req.use_session_vibe),
        "session_vibe": session_vibe if req.use_session_vibe else None,
        "export_path": str(export_root),
        "jpeg_folder": str(jpeg_dir),
        "raw_folder": str(raw_out),
        "errors": errors or None,
    }
    if export_opts["export_film_from_raw"]:
        resp["graded_from_raw_folder"] = str(graded_dir)
    from utils.json_safe import json_safe

    return json_safe(resp)


@router.get("/api/tasks/queue-backlog")
def queue_backlog():
    try:
        inspect = celery_client.control.inspect(timeout=1.0)
        active = inspect.active() or {}
        reserved = inspect.reserved() or {}
        scheduled = inspect.scheduled() or {}
    except Exception as e:
        return {
            "broker_url": _CELERY_BROKER,
            "result_backend": _CELERY_BACKEND,
            "workers": [],
            "totals": {
                "active": 0,
                "reserved": 0,
                "scheduled": 0,
                "redis_list_len": None,
            },
            "redis_error": str(e),
            "celery_unavailable": True,
        }

    workers = sorted(set(active.keys()) | set(reserved.keys()) | set(scheduled.keys()))
    worker_items = []
    total_active = total_reserved = total_scheduled = 0
    for w in workers:
        a = len(active.get(w, []) or [])
        r = len(reserved.get(w, []) or [])
        s = len(scheduled.get(w, []) or [])
        total_active += a
        total_reserved += r
        total_scheduled += s
        worker_items.append({"worker": w, "active": a, "reserved": r, "scheduled": s})

    redis_queue_len = None
    redis_error = None
    try:
        conn = celery_client.broker_connection().default_channel.client
        redis_queue_len = int(conn.llen("celery"))
    except Exception as e:
        redis_error = str(e)

    return {
        "broker_url": _CELERY_BROKER,
        "result_backend": _CELERY_BACKEND,
        "workers": worker_items,
        "totals": {
            "active": total_active,
            "reserved": total_reserved,
            "scheduled": total_scheduled,
            "redis_list_len": redis_queue_len,
        },
        "redis_error": redis_error,
        "celery_unavailable": False,
    }


@router.post("/api/ingest/check_new_images")
def check_new_images(
    config_path: str = Query(default=os.getenv("LIVEHOUSE_CONFIG", "configs/livehouse.yaml")),
    x_luma_token: str | None = Header(default=None, alias="X-Luma-Token"),
):
    """Recommended ingest hook: enqueue ``tasks.process_brain_ingested`` (seed jobs → ``tasks.run_job``)."""
    expected = os.getenv("LIVEHOUSE_INGEST_TOKEN", "").strip()
    if expected and (x_luma_token or "").strip() != expected:
        raise HTTPException(status_code=401, detail="invalid or missing X-Luma-Token")

    try:
        from utils.luma_brain import brain_connect

        conn = brain_connect()
        conn.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"brain db: {e}") from e

    task = celery_client.send_task(
        "tasks.process_brain_ingested",
        kwargs={"config_path": config_path or os.getenv("LIVEHOUSE_CONFIG", "configs/livehouse.yaml")},
    )
    return {
        "task_id": task.id,
        "task_name": "tasks.process_brain_ingested",
        "config_path": config_path,
    }


@router.post("/api/tasks/analyze")
def enqueue_analysis(
    config_path: str = Query(default="configs/livehouse.yaml"),
    source_dir: str | None = Query(default=None),
    max_workers: int | None = Query(default=None),
    enable_checkpoint: bool = Query(default=True),
):
    """
    API owns job creation (DB SSOT); Celery only executes ``tasks.run_job`` with ``job_id``.
    Response includes ``job_id`` and ``status=QUEUED`` so clients can query ``jobs`` / infra APIs immediately.
    """
    if not source_dir or not str(source_dir).strip():
        raise HTTPException(status_code=400, detail="source_dir is required")

    trace_id = new_trace_id("analyze_path")
    conn = brain_connect()
    try:
        job_id = create_analyze_path_job(
            conn,
            source_dir=str(source_dir).strip(),
            config_path=config_path,
            max_workers=max_workers,
            enable_checkpoint=enable_checkpoint,
            trace_id=trace_id,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"create_job failed: {e}") from e
    finally:
        conn.close()

    task = celery_client.send_task("tasks.run_job", args=[job_id])
    return {
        "ok": True,
        "job_id": job_id,
        "status": "QUEUED",
        "trace_id": trace_id,
        "run_task_id": task.id,
        "task_name": "tasks.run_job",
    }


@router.post("/api/tasks/curate")
def enqueue_curation(
    config_path: str = Query(default="configs/livehouse.yaml"),
    source_dir: str | None = Query(default=None),
    target_keepers: int | None = Query(default=None),
    max_inferences: int | None = Query(default=None),
    allow_escalation: bool | None = Query(default=None),
    planner: str | None = Query(default=None, description="heuristic (default) | llm"),
    planner_model: str | None = Query(default=None, description="override the LLM planner model (provider-native id)"),
):
    """
    Create a ``CURATE_PATH`` job (agentic culling loop) and dispatch ``tasks.run_job``.

    ``planner=llm`` drives the loop with the LLM tool-calling planner over the configured
    provider (heuristic fallback on bad output); omit it for the deterministic heuristic.
    The agent's per-step decisions stream into ``job_events`` — open the job timeline
    in the Infra Console (``/infra``) to watch inspect/analyze/escalate/finalize live.
    """
    if not source_dir or not str(source_dir).strip():
        raise HTTPException(status_code=400, detail="source_dir is required")

    agent_overrides: dict[str, Any] = {}
    if target_keepers is not None:
        agent_overrides["target_keepers"] = int(target_keepers)
    if max_inferences is not None:
        agent_overrides["max_inferences"] = int(max_inferences)
    if allow_escalation is not None:
        agent_overrides["allow_escalation"] = bool(allow_escalation)
    if planner is not None:
        pk = str(planner).strip().lower()
        if pk not in ("heuristic", "llm"):
            raise HTTPException(status_code=400, detail="planner must be 'heuristic' or 'llm'")
        agent_overrides["planner"] = pk
    if planner_model is not None and str(planner_model).strip():
        agent_overrides["planner_model"] = str(planner_model).strip()

    trace_id = new_trace_id("curate_path")
    conn = brain_connect()
    try:
        job_id = create_curate_path_job(
            conn,
            source_dir=str(source_dir).strip(),
            config_path=config_path,
            agent=agent_overrides or None,
            trace_id=trace_id,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"create_job failed: {e}") from e
    finally:
        conn.close()

    task = celery_client.send_task("tasks.run_job", args=[job_id])
    return {
        "ok": True,
        "job_id": job_id,
        "status": "QUEUED",
        "trace_id": trace_id,
        "run_task_id": task.id,
        "task_name": "tasks.run_job",
        "job_type": "CURATE_PATH",
        "planner": agent_overrides.get("planner", "heuristic"),
    }


@router.post("/api/tasks/prewarm-gallery-film")
def enqueue_prewarm_gallery_film():
    """Optional manual trigger; normally prewarm runs on gallery load and after analyze."""
    base = _runtime_base_dir()
    task_id = try_enqueue_gallery_cinestill_prewarm(source_dir=base)
    return {
        "ok": bool(task_id),
        "task_id": task_id,
        "task_name": "tasks.prewarm_gallery_cinestill",
        "previews_base": base,
        "skipped": task_id is None,
    }


@router.post("/api/tasks/luma-professional")
def enqueue_luma_workflow(raw_path: str, out_path: str):
    task = celery_client.send_task(
        "tasks.run_luma_professional_workflow",
        kwargs={"raw_path": raw_path, "out_path": out_path},
    )
    return {"task_id": task.id, "task_name": "tasks.run_luma_professional_workflow"}


@router.get("/api/tasks/{task_id}")
def get_task_status(task_id: str):
    async_result = celery_client.AsyncResult(task_id)
    return {
        "task_id": task_id,
        "state": async_result.state,
        "result": async_result.result if async_result.ready() else None,
    }


@router.get("/api/gallery/results")
def get_gallery_results(
    sort: str = Query("overall"),
    offset: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=5000),
    lite: bool = Query(
        True,
        description="Fast list: omit per-row PIL layout + RAW EXIF probing (recommended for Lab grid). Pass false for full enrichment.",
    ),
    dedupe: bool = Query(
        True,
        description="Fold near-duplicate bursts in the grid (see processing.gallery_view_dedupe).",
    ),
):
    active = _runtime_base_dir()
    items, total, start, end, has_more, total_raw = load_gallery_page(
        active, sort, offset, limit, lite=lite, dedupe=dedupe
    )
    film_prewarm_task_id = None
    if offset == 0 and total > 0:
        film_prewarm_task_id = try_enqueue_gallery_cinestill_prewarm(source_dir=active)
    from services.taste_profile import read_taste_profile

    taste_active = sort == "personalized" and read_taste_profile(active) is not None
    return {
        "count": total,
        "total_raw": total_raw,
        "dedupe_hidden": max(0, total_raw - total) if dedupe else 0,
        "dedupe_enabled": dedupe,
        "sort": sort,
        "taste_personalized": taste_active,
        "offset": start,
        "limit": limit,
        "next_offset": end if has_more else None,
        "has_more": has_more,
        "items": items,
        "film_prewarm_task_id": film_prewarm_task_id,
    }


@router.get("/api/debug/version")
def debug_version():
    active = _runtime_base_dir()
    return {
        "build": SERVER_BUILD,
        "startup_base_dir": BASE_DIR,
        "active_base_dir": active,
        "results_json": os.path.join(active, "analysis_results.json"),
    }


@router.get("/api/debug/orientation")
def debug_orientation(file_name: str = Query(...)):
    active = _runtime_base_dir()
    resolver = _path_resolver(active)
    sd, rh = resolver.session_and_raw_hint()
    resolved = resolver.resolve_raw(file_name)
    deg = read_orientation_degrees_from_raw(resolved)
    return {
        "file_name": file_name,
        "rotate_degrees": deg,
        "session_dir": str(sd),
        "raw_dir_hint": str(rh) if rh else None,
        "resolved_raw": str(resolved) if resolved else None,
        "raw_dir_candidates": [
            str(sd / "RAW"),
            str(sd / "Raw"),
            str(sd / "raw"),
            str(sd),
        ],
    }


@router.get("/api/gallery/similar")
def get_similar_photos(
    path: str = Query(..., description="Absolute or gallery-relative path of the source image"),
    top_k: int = Query(10, ge=1, le=50, description="Maximum results"),
    session_id: int | None = Query(None, description="Restrict corpus to one session (None = all)"),
):
    """Return visually similar photos ranked by CLIP cosine similarity.

    The source image is embedded on the fly if not already in the index.  Corpus
    embeddings must be pre-indexed via ``POST /api/gallery/embeddings/index``; photos
    without embeddings are silently skipped.

    Requires ``open-clip-torch`` to be installed.
    """
    from services.embedding_service import EmbeddingService
    from utils.luma_brain import brain_connect

    resolved = _resolve_gallery_image_path(path)
    if resolved is None:
        raise HTTPException(status_code=404, detail="image not found")

    if not EmbeddingService.is_available():
        raise HTTPException(
            status_code=503,
            detail="Embedding service unavailable — install open-clip-torch: pip install open-clip-torch",
        )

    conn = brain_connect()
    try:
        results = EmbeddingService.find_similar_to_path(
            conn,
            resolved,
            top_k=top_k,
            session_id=session_id,
            exclude_self=True,
        )
    finally:
        conn.close()

    return {
        "query_path": path,
        "model": "ViT-B-32",
        "session_id": session_id,
        "indexed_corpus_size": None,  # populated by index endpoint
        "results": results,
    }


@router.post("/api/gallery/embeddings/index")
def index_session_embeddings(
    session_id: int | None = Query(None, description="Session to index; None = latest active"),
    force_reindex: bool = Query(False, description="Re-generate embeddings even if already indexed"),
):
    """Batch-generate CLIP embeddings for all ANALYZED photos in a session.

    Call this once after an analyze job completes to populate the similarity index.
    Idempotent by default (skips already-indexed photos unless ``force_reindex=True``).

    Typical duration: ~100 ms/photo on CPU; runs in the API request thread.
    For large sessions (>500 photos) prefer triggering this as a background Celery task.
    """
    from services.embedding_service import EmbeddingService
    from utils.luma_brain import brain_connect

    if not EmbeddingService.is_available():
        raise HTTPException(
            status_code=503,
            detail="Embedding service unavailable — install open-clip-torch: pip install open-clip-torch",
        )

    conn = brain_connect()
    try:
        target_session_id = session_id
        if target_session_id is None:
            # Fall back to most recent session with analyzed photos.
            row = conn.execute(
                """
                SELECT session_id FROM photos
                WHERE status = 'ANALYZED' AND session_id IS NOT NULL
                ORDER BY updated_at DESC
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return {"ok": False, "error": "no analyzed photos found", "indexed": 0}
            target_session_id = row[0]

        result = EmbeddingService.index_session(
            conn, target_session_id, force_reindex=force_reindex
        )
    finally:
        conn.close()

    if not result.get("ok"):
        raise HTTPException(status_code=503, detail=result.get("error", "index_failed"))

    return result


from api.studio_routes import router as studio_router  # noqa: E402

router.include_router(studio_router)
