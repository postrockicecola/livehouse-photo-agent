"""Shared helpers for Gallery agent skills."""
from __future__ import annotations

import json
import logging
import os
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

from services.agent.skills.base import SkillResult

logger = logging.getLogger(__name__)

# Opt-out: LIVEHOUSE_AGENT_SEMANTIC_FALLBACK=0 disables CLIP text→image fallback.
_SEMANTIC_FALLBACK_ENV = "LIVEHOUSE_AGENT_SEMANTIC_FALLBACK"
_SEMANTIC_MIN_SIM = 0.22

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_DEFAULT_SYNONYMS_PATH = _REPO_ROOT / "data" / "agent" / "query_synonyms.jsonl"

# Canonical category labels written into ``analysis_results.json`` by
# ``gallery_integration`` (keys of ``paths.folders``). Disk folder *names* often
# look like ``AI_Best_90+`` (see ``configs/livehouse.yaml``), but the JSON field
# is the short form. Historical rows / tests may still store the AI_* folder name
# in ``category`` — treat those as aliases, not a second taxonomy.
_KNOWN_CATEGORIES = ("best", "keep", "trash")
_CATEGORY_ALIASES = {
    "best": "best",
    "keep": "keep",
    "trash": "trash",
    "ai_best_90+": "best",
    "ai_keep_60-90": "keep",
    "ai_trash_below60": "trash",
    "ai_best": "best",
    "ai_keep": "keep",
    "ai_trash": "trash",
}
_SORT_KEYS = (
    "overall",
    "energy",
    "technical",
    "composition",
    "deliverable_subject",
    "atmosphere_impact",
    "moment_peak",
)
_TRASH_HINTS = ("blur", "blurry", "out of focus", "过曝", "overex", "糊", "失焦", "exposure")
# Pipeline / Stage2/3 ops labels — not VLM semantic content tags.
_PIPELINE_TAGS = frozenset(
    {
        "low_quality",
        "stage2_prefilter",
        "technical_issue",
        "stage3_skipped_gating",
        "near_duplicate",
        "stage2_dedup",
    }
)
_BOILERPLATE_REASON_PREFIXES = (
    "near-duplicate",
    "stage3 skipped",
    "stage 2",
    "vlm skipped",
    "technical issue:",
)

# Fallback when ``data/agent/query_synonyms.jsonl`` is missing — identical groups.
_QUERY_SYNONYMS_FALLBACK: tuple[tuple[str, ...], ...] = (
    ("鼓手", "打鼓", "鼓点", "架子鼓", "drummer", "drums", "drum kit", "drumming"),
    ("吉他手", "吉他", "弹琴", "指弹", "guitarist", "guitar", "electric guitar"),
    ("贝斯", "贝斯手", "bass", "bassist"),
    ("歌手", "主唱", "人声", "singer", "vocalist", "vocals"),
    ("全景", "舞台全景", "大场面", "wide stage", "wide shot", "establishing", "panorama"),
    ("观众", "灯海", "crowd", "audience", "pit"),
    ("前排", "前排互动", "front row", "barricade", "mosh"),
    ("逆光", "剪影", "轮廓光", "backlight", "silhouette", "rim light", "backlit"),
    ("特写", "近景", "close-up", "closeup", "portrait"),
    ("气氛", "氛围", "atmosphere", "energy", "vibe"),
    (
        "孤独",
        "孤独感",
        "寂寞",
        "疏离",
        "落寞",
        "lonely",
        "loneliness",
        "solitude",
        "solitary",
        "isolation",
        "isolated",
        "alone",
    ),
    ("宁静", "安静", "平静", "calm", "quiet", "serene", "peaceful"),
    ("热烈", "狂欢", "沸腾", "euphoric", "euphoria", "ecstatic", "raucous"),
    ("忧郁", "忧伤", "melancholy", "melancholic", "somber", "moody"),
    ("紧张", "紧绷", "tense", "tension", "anxious"),
    ("慢门", "慢快门", "长曝光", "拖影", "光轨", "slow shutter", "long exposure", "light trail", "light trails"),
)


@lru_cache(maxsize=4)
def _load_query_synonyms_cached(path_str: str) -> tuple[tuple[str, ...], ...]:
    p = Path(path_str)
    if not p.is_file():
        return _QUERY_SYNONYMS_FALLBACK
    groups: list[tuple[str, ...]] = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            obj = json.loads(line)
            terms = obj.get("terms") if isinstance(obj, dict) else None
            if not isinstance(terms, list) or not terms:
                continue
            cleaned = tuple(str(t) for t in terms if str(t).strip())
            if cleaned:
                groups.append(cleaned)
    except Exception:
        logger.warning("failed to load query synonyms from %s; using fallback", p)
        return _QUERY_SYNONYMS_FALLBACK
    return tuple(groups) if groups else _QUERY_SYNONYMS_FALLBACK


def load_query_synonyms(path: str | Path | None = None) -> tuple[tuple[str, ...], ...]:
    """Load synonym groups from JSONL (one ``{"terms": [...]}`` object per line)."""
    p = Path(path) if path is not None else _DEFAULT_SYNONYMS_PATH
    return _load_query_synonyms_cached(str(p.resolve()) if p.exists() else str(p))


# Backward-compatible module alias (loaded once at import).
_QUERY_SYNONYMS = load_query_synonyms()

_SLOW_SHUTTER_KEYS = (
    "慢门",
    "慢快门",
    "长曝光",
    "拖影",
    "光轨",
    "slow shutter",
    "long exposure",
    "light trail",
    "light trails",
    "light painting",
)
# Livehouse "慢门/长曝光" — require slower than typical handheld concert (1/20–1/30).
_SLOW_SHUTTER_MIN_S = 1.0 / 15.0


def _expand_query_terms(query: str) -> list[str]:
    """Turn a user/query string into OR-matched terms (synonyms + tokens)."""
    q = (query or "").strip().lower()
    if not q:
        return []
    terms: list[str] = []
    seen: set[str] = set()

    def _add(t: str) -> None:
        t = t.strip().lower()
        if len(t) < 2 or t in seen:
            return
        seen.add(t)
        terms.append(t)

    _add(q)
    for group in load_query_synonyms():
        if any(k.lower() in q for k in group):
            for k in group:
                _add(k)
    # Space / punctuation tokens (English phrases).
    for tok in q.replace("，", " ").replace(",", " ").replace("、", " ").split():
        _add(tok)
    return terms


def _style_intent(query: str) -> str | None:
    """Style intents that need structured signals (e.g. EXIF shutter)."""
    q = (query or "").strip().lower()
    if not q:
        return None
    if any(k in q for k in _SLOW_SHUTTER_KEYS):
        return "slow_shutter"
    return None


def _is_boilerplate_reason(text: str) -> bool:
    s = (text or "").strip().lower()
    if not s:
        return True
    return any(s.startswith(p) for p in _BOILERPLATE_REASON_PREFIXES)


def _is_pipeline_tag(tag: str) -> bool:
    return str(tag).strip().lower() in _PIPELINE_TAGS


def _load_rows(base_dir: str) -> list[dict[str, Any]]:
    """Fresh, normalized rows from the session's analysis_results.json."""
    from services.result_service import load_raw_results, normalize_scores

    rows = load_raw_results(base_dir)
    out: list[dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        row = dict(r)
        try:
            normalize_scores(row)
        except Exception:
            continue
        out.append(row)
    return out


def _caption(row: dict[str, Any]) -> str:
    rb = row.get("reason_bilingual") or {}
    if isinstance(rb, dict):
        cap = rb.get("zh") or rb.get("en")
        if cap and not _is_boilerplate_reason(str(cap)):
            return str(cap)
    reason = str(row.get("reason") or "")
    if reason and not _is_boilerplate_reason(reason):
        return reason
    return ""


def _dim(row: dict[str, Any], key: str) -> float:
    """Read a score / Stage3 dimension from flattened or nested row fields."""
    if key == "overall":
        try:
            return float(row.get("overall_score") or 0.0)
        except (TypeError, ValueError):
            return 0.0
    for container in (row, row.get("scores"), row.get("dimensions")):
        if not isinstance(container, dict):
            continue
        if container.get(key) is None:
            continue
        try:
            return float(container.get(key) or 0.0)
        except (TypeError, ValueError):
            continue
    return 0.0


def _pick_why(row: dict[str, Any], *, sort_by: str, recipe: str) -> str:
    """One-line explainability for a shortlisted frame."""
    overall = _dim(row, "overall")
    parts = [f"overall {overall:.0f}"]
    if recipe == "social" or sort_by == "deliverable_subject":
        parts.append(f"deliverable {_dim(row, 'deliverable_subject'):.1f}")
        parts.append(f"tech {_dim(row, 'technical'):.1f}")
    elif recipe == "energy" or sort_by == "atmosphere_impact":
        parts.append(f"atmosphere {_dim(row, 'atmosphere_impact'):.1f}")
        if _dim(row, "energy") > 0:
            parts.append(f"energy {_dim(row, 'energy'):.1f}")
    elif recipe == "peak" or sort_by == "moment_peak":
        parts.append(f"moment {_dim(row, 'moment_peak'):.1f}")
    elif recipe == "deliverable":
        parts.append(f"deliverable {_dim(row, 'deliverable_subject'):.1f}")
    else:
        if _dim(row, sort_by) and sort_by != "overall":
            parts.append(f"{sort_by} {_dim(row, sort_by):.1f}")
    cap = _caption(row)
    if cap:
        parts.append(cap[:40])
    return " · ".join(parts)


def _record(row: dict[str, Any], *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Compact, model-friendly view of one photo."""
    rec = {
        "file": row.get("file"),
        "overall_score": round(_dim(row, "overall"), 1),
        "energy": round(_dim(row, "energy"), 1),
        "technical": round(_dim(row, "technical"), 1),
        "composition": round(_dim(row, "composition"), 1),
        "deliverable_subject": round(_dim(row, "deliverable_subject"), 1),
        "atmosphere_impact": round(_dim(row, "atmosphere_impact"), 1),
        "moment_peak": round(_dim(row, "moment_peak"), 1),
        "category": row.get("category"),
        "tags": [t for t in (row.get("tags") or []) if not _is_pipeline_tag(str(t))],
        "caption": _caption(row),
    }
    mood = [str(t) for t in (row.get("mood_tags") or []) if str(t).strip()]
    if mood:
        rec["mood_tags"] = mood
    if extra:
        rec.update(extra)
    return rec


def _text_blob(row: dict[str, Any]) -> str:
    tags = " ".join(str(t) for t in (row.get("tags") or []) if not _is_pipeline_tag(str(t)))
    mood = " ".join(str(t) for t in (row.get("mood_tags") or []) if str(t).strip())
    rb = row.get("reason_bilingual") or {}
    en = ""
    zh = ""
    if isinstance(rb, dict):
        en = str(rb.get("en") or "")
        zh = str(rb.get("zh") or "")
        if _is_boilerplate_reason(en):
            en = ""
        if _is_boilerplate_reason(zh):
            zh = ""
    reason = str(row.get("reason") or "")
    if _is_boilerplate_reason(reason):
        reason = ""
    return f"{tags} {mood} {_caption(row)} {zh} {en} {reason}".lower()


def _resolve_raw_dir(previews_dir: str | Path) -> Path | None:
    """Sibling ``RAW/`` next to a session ``Previews/`` folder, when present."""
    base = Path(previews_dir).expanduser().resolve()
    candidates = [base.parent / "RAW", base / "RAW"]
    for cand in candidates:
        if cand.is_dir():
            return cand
    return None


def _load_exposure_times(previews_dir: str) -> dict[str, float]:
    """Map preview basename stem → ExposureTime (seconds) from sibling RAW EXIF.

    Cached under ``Previews/.luma_exposure_cache.json``. Empty when RAW/exiftool missing.
    """
    base = Path(previews_dir).expanduser().resolve()
    cache_path = base / ".luma_exposure_cache.json"
    raw_dir = _resolve_raw_dir(base)
    if raw_dir is None:
        return {}

    raw_files = sorted(
        [p for p in raw_dir.iterdir() if p.is_file() and p.suffix.lower() in {".arw", ".dng", ".nef", ".cr2", ".cr3", ".raf", ".orf", ".rw2"}]
    )
    if not raw_files:
        return {}

    cache_key = f"{raw_dir}:{len(raw_files)}:{max(p.stat().st_mtime for p in raw_files):.0f}"
    if cache_path.is_file():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if isinstance(cached, dict) and cached.get("_key") == cache_key and isinstance(cached.get("times"), dict):
                return {str(k): float(v) for k, v in cached["times"].items()}
        except Exception:
            pass

    times: dict[str, float] = {}
    try:
        for i in range(0, len(raw_files), 200):
            chunk = raw_files[i : i + 200]
            proc = subprocess.run(
                ["exiftool", "-ExposureTime", "-n", "-T", "-q", *[str(p) for p in chunk]],
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode not in (0, None) and not proc.stdout.strip():
                logger.info("exiftool shutter scan failed: %s", (proc.stderr or "")[:200])
                break
            lines = proc.stdout.splitlines()
            for path, line in zip(chunk, lines):
                try:
                    et = float(line.strip().split("\t")[0])
                except (TypeError, ValueError):
                    continue
                if et > 0:
                    times[path.stem] = et
    except FileNotFoundError:
        logger.info("exiftool not on PATH; slow-shutter EXIF search disabled")
        return {}

    try:
        cache_path.write_text(
            json.dumps({"_key": cache_key, "times": times}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass
    return times


def _format_shutter(seconds: float) -> str:
    if seconds <= 0:
        return "?"
    if seconds >= 1:
        return f"{seconds:.2g}s"
    denom = max(1, int(round(1.0 / seconds)))
    return f"1/{denom}s"


def _search_slow_shutter(
    rows: list[dict[str, Any]],
    *,
    base_dir: str,
    limit: int,
) -> SkillResult:
    """Rank by RAW ExposureTime — do not use weak CLIP for 慢门."""
    times = _load_exposure_times(base_dir)
    by_stem = {Path(str(r.get("file") or "")).stem: r for r in rows if r.get("file")}
    scored: list[tuple[float, dict[str, Any]]] = []
    for stem, et in times.items():
        row = by_stem.get(stem)
        if row is None:
            continue
        scored.append((et, row))
    scored.sort(key=lambda pair: pair[0], reverse=True)

    threshold = _SLOW_SHUTTER_MIN_S
    hard = [(et, r) for et, r in scored if et >= threshold]
    vals = [et for et, _ in scored]
    stats = {
        "n_with_exif": len(vals),
        "max_s": max(vals) if vals else None,
        "min_s": min(vals) if vals else None,
        "threshold_s": threshold,
        "threshold_label": _format_shutter(threshold),
        "ge_threshold": len(hard),
    }
    slowest_examples = [
        {
            "file": r.get("file"),
            "exposure_s": round(et, 4),
            "shutter": _format_shutter(et),
        }
        for et, r in scored[:8]
    ]

    if not times:
        summary = (
            "0 photo(s) matched for slow-shutter. Could not read ExposureTime from sibling "
            "RAW/ (missing folder or exiftool)."
        )
        return SkillResult(
            ok=True,
            output=summary,
            metadata={
                "rows": [],
                "count": 0,
                "files": [],
                "ui_action": "search",
                "style_intent": "slow_shutter",
                "shutter_stats": stats,
            },
        )

    if not hard:
        max_lab = _format_shutter(stats["max_s"]) if stats["max_s"] else "?"
        # No slowest_examples when count=0 — avoids the model treating relative-slow
        # frames as true 慢门 matches.
        summary = (
            f"0 photo(s) matched for slow-shutter / 长曝光. "
            f"Scanned {stats['n_with_exif']} RAW ExposureTime values; slowest is {max_lab} "
            f"(threshold {_format_shutter(threshold)}). This session has no true 慢门 frames."
        )
        return SkillResult(
            ok=True,
            output=summary,
            metadata={
                "rows": [],
                "count": 0,
                "files": [],
                "ui_action": "search",
                "style_intent": "slow_shutter",
                "shutter_stats": stats,
            },
        )

    top_pairs = hard[:limit]
    top = [
        _record(r, extra={"exposure_s": round(et, 4), "shutter": _format_shutter(et)})
        for et, r in top_pairs
    ]
    files = [str(r["file"]) for r in top if r.get("file")]
    summary = (
        f"{len(hard)} photo(s) with ExposureTime ≥ {_format_shutter(threshold)}; "
        f"showing top {len(top)} slowest (EXIF from RAW/, not CLIP)."
    )
    return SkillResult(
        ok=True,
        output=summary,
        metadata={
            "rows": top,
            "count": len(hard),
            "files": files,
            "ui_action": "search",
            "style_intent": "slow_shutter",
            "shutter_stats": stats,
            "slowest_examples": slowest_examples,
        },
    )


def _query_hit_score(blob: str, terms: list[str]) -> int:
    """How many expanded terms hit; longer terms count more."""
    if not terms:
        return 1
    score = 0
    for t in terms:
        if t in blob:
            score += max(1, min(4, len(t) // 2))
    return score


def _normalize_category(raw: str) -> str:
    """Map folder-style / legacy labels onto canonical ``best|keep|trash``.

    ``AI_Best_90+`` / ``AI_Keep_60-90`` / ``AI_Trash_Below60`` are the on-disk folder
    names from ``paths.folders``; ``best`` / ``keep`` / ``trash`` are the values
    written into ``analysis_results.json``. Same score-band buckets — not two taxonomies.
    Unknown strings are returned stripped (filter will fail closed on mismatch).
    """
    s = str(raw or "").strip()
    if not s:
        return ""
    return _CATEGORY_ALIASES.get(s.lower(), s)


def _is_trash_category(raw: str) -> bool:
    return _normalize_category(raw) == "trash" or "trash" in str(raw or "").lower()


def _filter_rows(rows: list[dict[str, Any]], args: dict[str, Any]) -> list[dict[str, Any]]:
    min_score = args.get("min_score")
    max_score = args.get("max_score")
    min_energy = args.get("min_energy")
    max_energy = args.get("max_energy")
    min_technical = args.get("min_technical")
    max_technical = args.get("max_technical")
    min_composition = args.get("min_composition")
    max_composition = args.get("max_composition")
    min_deliverable = args.get("min_deliverable")
    min_atmosphere = args.get("min_atmosphere")
    min_moment_peak = args.get("min_moment_peak")
    tag = str(args.get("tag") or "").strip().lower()
    query = str(args.get("query") or "").strip().lower()
    query_terms = _expand_query_terms(query) if query else []
    category = _normalize_category(str(args.get("category") or ""))
    exclude_trash = bool(args.get("exclude_trash"))
    exclude_low_quality = bool(args.get("exclude_low_quality"))

    scored: list[tuple[int, float, dict[str, Any]]] = []
    sort_by = str(args.get("_sort_by") or "overall")
    if sort_by not in _SORT_KEYS:
        sort_by = "overall"

    for row in rows:
        overall = _dim(row, "overall")
        energy = _dim(row, "energy")
        technical = _dim(row, "technical")
        composition = _dim(row, "composition")
        deliverable = _dim(row, "deliverable_subject")
        atmosphere = _dim(row, "atmosphere_impact")
        moment = _dim(row, "moment_peak")
        cat = str(row.get("category") or "")
        cat_norm = _normalize_category(cat)
        blob = _text_blob(row)

        if min_score is not None and overall < float(min_score):
            continue
        if max_score is not None and overall > float(max_score):
            continue
        if min_energy is not None and energy < float(min_energy):
            continue
        if max_energy is not None and energy > float(max_energy):
            continue
        if min_technical is not None and technical < float(min_technical):
            continue
        if max_technical is not None and technical > float(max_technical):
            continue
        if min_composition is not None and composition < float(min_composition):
            continue
        if max_composition is not None and composition > float(max_composition):
            continue
        # Stage3 dims are often missing on older rows — only enforce when the signal exists (>0).
        if min_deliverable is not None and deliverable > 0 and deliverable < float(min_deliverable):
            continue
        if min_atmosphere is not None and atmosphere > 0 and atmosphere < float(min_atmosphere):
            continue
        if min_moment_peak is not None and moment > 0 and moment < float(min_moment_peak):
            continue
        if category and cat_norm != category:
            continue
        if exclude_trash and _is_trash_category(cat):
            continue
        if exclude_low_quality:
            if any(h in blob for h in _TRASH_HINTS) or technical < 5.0 or overall < 55.0:
                continue
        if tag:
            tags = [str(t).lower() for t in (row.get("tags") or [])]
            if not any(tag in t for t in tags):
                continue
        q_score = 0
        if query_terms:
            q_score = _query_hit_score(blob, query_terms)
            if q_score <= 0:
                continue
        scored.append((q_score, _dim(row, sort_by), overall, row))

    # Stronger semantic hit first, then requested score, then overall as tie-break.
    scored.sort(key=lambda pair: (pair[0], pair[1], pair[2]), reverse=True)
    return [row for _, _, _, row in scored]


def _maybe_dedupe(rows: list[dict[str, Any]], base_dir: str, enabled: bool) -> list[dict[str, Any]]:
    if not enabled or len(rows) < 2:
        return rows
    try:
        from services.gallery_dedupe import apply_gallery_view_dedupe, gallery_view_dedupe_settings

        settings = gallery_view_dedupe_settings(None)
        settings = {**settings, "enabled": True, "keep_per_cluster": 1}
        kept_idx, _, _ = apply_gallery_view_dedupe(rows, "overall", settings=settings)
        return [rows[i] for i in kept_idx if 0 <= i < len(rows)]
    except Exception:
        # Filename burst fallback: keep best overall per trailing-number cluster.
        try:
            from services.diversity_selector import _cluster_map_burst, _trailing_burst_num
        except Exception:
            return rows

        ids = [str(r.get("file") or "") for r in rows]
        if not any(_trailing_burst_num(i) is not None for i in ids):
            return rows
        cluster_of = _cluster_map_burst(ids, burst_window=3)
        best: dict[int, dict[str, Any]] = {}
        orphans: list[dict[str, Any]] = []
        for row in rows:
            fid = str(row.get("file") or "")
            cid = cluster_of.get(fid)
            if cid is None:
                orphans.append(row)
                continue
            prev = best.get(cid)
            if prev is None or _dim(row, "overall") > _dim(prev, "overall"):
                best[cid] = row
        survivors = [*best.values(), *orphans]
        survivors.sort(key=lambda r: _dim(r, "overall"), reverse=True)
        return survivors


def semantic_fallback_enabled() -> bool:
    """CLIP text→image fallback when synonym/text search is empty (default on)."""
    raw = (os.environ.get(_SEMANTIC_FALLBACK_ENV) or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _preview_path_for_row(base_dir: str, row: dict[str, Any]) -> Path | None:
    name = str(row.get("file") or "").strip()
    if not name:
        return None
    base = Path(base_dir).expanduser().resolve()
    candidates = [
        base / Path(name).name,
        base / name,
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


def semantic_fallback_rows(
    rows: list[dict[str, Any]],
    *,
    base_dir: str,
    query: str,
    limit: int,
    min_sim: float = _SEMANTIC_MIN_SIM,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Rank preview images by CLIP text similarity when tag search misses.

    Returns ``(matched_rows, meta)``. Empty when CLIP unavailable or no previews.
    Slow-shutter / EXIF paths must NOT call this.
    """
    meta: dict[str, Any] = {"retrieval": "clip_text", "attempted": True}
    q = (query or "").strip()
    if not q or not semantic_fallback_enabled():
        meta["attempted"] = False
        return [], meta
    try:
        from services.embedding_service import EmbeddingService
    except Exception:
        meta["available"] = False
        return [], meta
    if not EmbeddingService.is_available():
        meta["available"] = False
        return [], meta
    meta["available"] = True

    paths: list[Path] = []
    by_name: dict[str, dict[str, Any]] = {}
    for row in rows:
        path = _preview_path_for_row(base_dir, row)
        if path is None:
            continue
        paths.append(path)
        by_name[path.name.lower()] = row
    if not paths:
        meta["n_previews"] = 0
        return [], meta

    cache_dir = Path(base_dir).expanduser().resolve() / ".clip_text_cache"
    hits = EmbeddingService.find_similar_to_text(
        q,
        paths,
        top_k=max(1, int(limit)),
        cache_dir=cache_dir,
    )
    out: list[dict[str, Any]] = []
    sims: list[float] = []
    for hit in hits:
        sim = float(hit.get("similarity") or 0.0)
        if sim < float(min_sim):
            continue
        row = by_name.get(str(hit.get("file_name") or "").lower())
        if row is None:
            continue
        out.append(row)
        sims.append(sim)
    meta["n_previews"] = len(paths)
    meta["min_sim"] = float(min_sim)
    meta["hit_sims"] = sims[:12]
    return out, meta


def _basename(path_or_name: str) -> str:
    from pathlib import Path

    return Path(str(path_or_name or "").strip()).name


def _unique_basenames(raw_list: Any) -> list[str]:
    if not isinstance(raw_list, list):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for x in raw_list:
        b = _basename(str(x or ""))
        if not b or b in seen:
            continue
        seen.add(b)
        out.append(b)
    return out


def _resolve_photo_target(base_dir: str, args: dict[str, Any]) -> tuple[str | None, str | None]:
    """Resolve a single photo basename for per-photo skills.

    Priority: ``file`` → ``focus_file`` → single ``selected_files`` → single curation like
    → first of ``last_files`` (recent search shortlist).
    Returns ``(basename, error)``.
    """
    for key in ("file", "focus_file"):
        raw = str(args.get(key) or "").strip()
        if raw:
            return _basename(raw), None

    selected = _unique_basenames(args.get("selected_files"))
    if len(selected) == 1:
        return selected[0], None

    try:
        from utils.gallery_curation import read_gallery_curation

        cur = read_gallery_curation(base_dir) or {}
        liked = _unique_basenames(cur.get("selected_keys") or [])
        if len(liked) == 1:
            return liked[0], None
    except Exception:
        liked = []

    # Recent gallery_search / select shortlist — use the top hit for 「这张」.
    last_files = _unique_basenames(args.get("last_files"))
    if len(last_files) == 1:
        return last_files[0], None
    if len(last_files) > 1:
        return last_files[0], None

    if len(selected) > 1 or len(liked) > 1:
        return None, "当前有多张选中；请先打开一张照片预览，再说「最适合这张的胶片风格」"

    return None, "请先在 Gallery 打开或选中一张照片，再说「最适合这张的胶片风格」"

