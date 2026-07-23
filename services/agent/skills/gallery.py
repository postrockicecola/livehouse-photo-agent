"""Gallery skills: search/select/vibe/export for the ChatDock + landing hero prompts.

Search remains grounded in ``analysis_results.json``. Select / vibe / export write through
the same persistence paths the Gallery UI uses (curation JSON, session vibe, export API).
"""
from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

from services.agent.skills.base import SkillRegistry, SkillResult

logger = logging.getLogger(__name__)

_KNOWN_CATEGORIES = ("AI_Best_90+", "AI_Keep_60-90", "AI_Trash_Below60", "best", "keep", "trash")
_SORT_KEYS = ("overall", "energy", "technical", "composition")
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

# Chinese / English livehouse synonyms — VLM tags are often English-only.
_QUERY_SYNONYMS: tuple[tuple[str, ...], ...] = (
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
    ("慢门", "慢快门", "长曝光", "拖影", "光轨", "slow shutter", "long exposure", "light trail", "light trails"),
)

# Framing intents need contrastive CLIP (pos − neg); bare "wide" is too weak alone.
_WIDE_KEYS = ("全景", "舞台全景", "大场面", "wide stage", "wide shot", "establishing", "panorama", "远景")
_CLOSE_KEYS = ("特写", "近景", "close-up", "closeup", "portrait", "tight crop")
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
_CLIP_FRAMING: dict[str, tuple[str, str]] = {
    "wide": (
        "wide establishing shot of a live concert stage from the audience, "
        "full stage panorama with band and lights",
        "tight close-up of a musician face or instrument filling the frame, portrait crop",
    ),
    "closeup": (
        "tight close-up of a musician performing, face or instrument filling the frame",
        "wide establishing shot of a full concert stage from far away, panorama",
    ),
}
_CLIP_SUBJECT: tuple[tuple[tuple[str, ...], str], ...] = (
    (("鼓手", "打鼓", "鼓点", "架子鼓", "drummer", "drums", "drumming"), "drummer playing drums on stage"),
    (("吉他手", "吉他", "弹琴", "指弹", "guitarist", "guitar"), "electric guitarist playing guitar on stage"),
    (("贝斯", "贝斯手", "bass", "bassist"), "bassist playing bass guitar on stage"),
    (("歌手", "主唱", "人声", "singer", "vocalist"), "lead singer with microphone on stage"),
    (("观众", "灯海", "crowd", "audience"), "concert crowd audience with phone lights"),
)


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
    for group in _QUERY_SYNONYMS:
        if any(k.lower() in q for k in group):
            for k in group:
                _add(k)
    # Space / punctuation tokens (English phrases).
    for tok in q.replace("，", " ").replace(",", " ").replace("、", " ").split():
        _add(tok)
    return terms


def _framing_intent(query: str) -> str | None:
    """Return ``wide`` / ``closeup`` when the user asks for a shot type."""
    q = (query or "").strip().lower()
    if not q:
        return None
    # Prefer close-up when both appear (e.g. 全景里的特写 is rare; 吉他手特写 is common).
    if any(k in q for k in _CLOSE_KEYS):
        return "closeup"
    if any(k in q for k in _WIDE_KEYS):
        return "wide"
    return None


def _style_intent(query: str) -> str | None:
    """Non-CLIP style intents that need structured signals (e.g. EXIF shutter)."""
    q = (query or "").strip().lower()
    if not q:
        return None
    if any(k in q for k in _SLOW_SHUTTER_KEYS):
        return "slow_shutter"
    return None


def _subject_clip_prompt(query: str) -> str | None:
    q = (query or "").strip().lower()
    for keys, prompt in _CLIP_SUBJECT:
        if any(k.lower() in q for k in keys):
            return prompt
    return None


def _clip_prompts(query: str, terms: list[str]) -> tuple[str, str | None, str | None]:
    """Return ``(positive_clip, negative_clip|None, framing_intent|None)``."""
    # Style intents must not fall through to weak CLIP synonym dumps.
    if _style_intent(query) == "slow_shutter":
        return (
            "artistic long exposure concert photo with intentional motion blur and light trails",
            "tack sharp frozen concert photo with crisp details",
            "slow_shutter",
        )
    framing = _framing_intent(query)
    subject = _subject_clip_prompt(query)
    if framing and framing in _CLIP_FRAMING:
        pos, neg = _CLIP_FRAMING[framing]
        if subject and framing == "closeup":
            # "吉他手特写" → subject close-up, not generic portrait.
            pos = f"tight close-up of a {subject}"
        elif subject and framing == "wide":
            pos = f"wide establishing shot including {subject}, full stage panorama"
        return pos, neg, framing
    if subject:
        # Subject-only: contrast against the opposite framing / wrong subject loosely.
        return subject, "blurry unusable photo, empty stage with no performers", None
    ascii_terms = [
        t for t in terms if t and all(ord(c) < 128 for c in t) and any(c.isalpha() for c in t)
    ]
    # Prefer multi-word phrases; drop ultra-short ambiguous tokens like bare "wide".
    phrases = [t for t in ascii_terms if " " in t or len(t) >= 5]
    joined = " ".join(dict.fromkeys(phrases or ascii_terms))
    return (joined[:240] if joined else (query or "").strip()), None, None


def _clip_query_text(query: str, terms: list[str]) -> str:
    """Back-compat: positive CLIP prompt only."""
    pos, _, _ = _clip_prompts(query, terms)
    return pos


def _is_boilerplate_reason(text: str) -> bool:
    s = (text or "").strip().lower()
    if not s:
        return True
    return any(s.startswith(p) for p in _BOILERPLATE_REASON_PREFIXES)


def _is_pipeline_tag(tag: str) -> bool:
    return str(tag).strip().lower() in _PIPELINE_TAGS


def _is_quality_rejected(row: dict[str, Any]) -> bool:
    tags = {str(t).lower() for t in (row.get("tags") or [])}
    cat = str(row.get("category") or "").lower()
    return "low_quality" in tags or "stage2_prefilter" in tags or "trash" in cat


def _prefer_visual_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop Stage2 reject / trash frames for CLIP when possible."""
    kept = [r for r in rows if not _is_quality_rejected(r)]
    return kept or rows


def _cap_rag_candidates(
    candidates: list[dict[str, Any]],
    text_hits: list[dict[str, Any]],
    *,
    soft_cap: int = 60,
    empty_text_cap: int = 180,
) -> list[dict[str, Any]]:
    """Limit CLIP corpus; enlarge when text retrieval found nothing."""
    # Semantic queries with no tag hits should not burn CLIP budget on blur rejects.
    pool = candidates if text_hits else _prefer_visual_candidates(candidates)
    cap = empty_text_cap if not text_hits else soft_cap
    if len(pool) <= cap:
        return pool
    seen: set[str] = set()
    capped: list[dict[str, Any]] = []
    for r in text_hits + sorted(pool, key=lambda x: _dim(x, "overall"), reverse=True):
        fn = str(r.get("file") or "")
        if not fn or fn in seen:
            continue
        seen.add(fn)
        capped.append(r)
        if len(capped) >= cap:
            break
    return capped


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
    if key == "overall":
        return float(row.get("overall_score") or 0.0)
    return float(row.get(key) or 0.0)


def _record(row: dict[str, Any], *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Compact, model-friendly view of one photo."""
    rec = {
        "file": row.get("file"),
        "overall_score": round(_dim(row, "overall"), 1),
        "energy": round(_dim(row, "energy"), 1),
        "technical": round(_dim(row, "technical"), 1),
        "composition": round(_dim(row, "composition"), 1),
        "category": row.get("category"),
        "tags": [t for t in (row.get("tags") or []) if not _is_pipeline_tag(str(t))],
        "caption": _caption(row),
    }
    if extra:
        rec.update(extra)
    return rec


def _text_blob(row: dict[str, Any]) -> str:
    tags = " ".join(str(t) for t in (row.get("tags") or []) if not _is_pipeline_tag(str(t)))
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
    return f"{tags} {_caption(row)} {zh} {en} {reason}".lower()


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
            "RAW/ (missing folder or exiftool). Do not invent 慢门 matches from Stage3-skip captions."
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
                "slowest_examples": [],
            },
        )

    if not hard:
        max_lab = _format_shutter(stats["max_s"]) if stats["max_s"] else "?"
        summary = (
            f"0 photo(s) matched for slow-shutter / 长曝光. "
            f"Scanned {stats['n_with_exif']} RAW ExposureTime values; slowest is {max_lab} "
            f"(threshold {_format_shutter(threshold)}). This session has no true 慢门 frames — "
            "do NOT list Stage3-skipped / near-duplicate photos as matches, and do NOT claim "
            "CLIP found slow-shutter content."
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
                "slowest_examples": slowest_examples,
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


def _filter_rows(rows: list[dict[str, Any]], args: dict[str, Any]) -> list[dict[str, Any]]:
    min_score = args.get("min_score")
    max_score = args.get("max_score")
    min_energy = args.get("min_energy")
    max_energy = args.get("max_energy")
    min_technical = args.get("min_technical")
    max_technical = args.get("max_technical")
    min_composition = args.get("min_composition")
    max_composition = args.get("max_composition")
    tag = str(args.get("tag") or "").strip().lower()
    query = str(args.get("query") or "").strip().lower()
    query_terms = _expand_query_terms(query) if query else []
    category = str(args.get("category") or "").strip()
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
        cat = str(row.get("category") or "")
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
        if category and cat != category:
            continue
        if exclude_trash and ("Trash" in cat or cat.lower() == "trash"):
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
        scored.append((q_score, _dim(row, sort_by), row))

    # Stronger semantic hit first, then requested score.
    scored.sort(key=lambda pair: (pair[0], pair[1]), reverse=True)
    return [row for _, _, row in scored]


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


class GallerySearchSkill:
    name = "gallery_search"
    description = (
        "Search the current session's analyzed photos (multimodal RAG). Filter by score bands "
        "(overall / energy / technical / composition), tag substring, free-text query "
        "(tags+caption+reason, Chinese↔English synonyms), optional CLIP visual similarity, "
        "category, exclude trash/low-quality, and burst dedupe. When query is set, default "
        "mode=hybrid fuses text + visual and returns citations. Sort by "
        "overall|energy|technical|composition. Returns top-N with scores, tags, caption, citations."
    )
    parameters = {
        "type": "object",
        "properties": {
            "min_score": {"type": "number", "description": "Minimum overall score (0-100)."},
            "max_score": {"type": "number", "description": "Maximum overall score (0-100)."},
            "min_energy": {"type": "number"},
            "max_energy": {"type": "number"},
            "min_technical": {"type": "number"},
            "max_technical": {"type": "number"},
            "min_composition": {"type": "number"},
            "max_composition": {"type": "number"},
            "tag": {"type": "string", "description": "Only photos whose tags contain this substring."},
            "query": {
                "type": "string",
                "description": (
                    "Free-text / semantic query. Text matches tags/caption/reason with "
                    "Chinese↔English synonyms; hybrid mode also ranks via CLIP text→image."
                ),
            },
            "mode": {
                "type": "string",
                "enum": ["hybrid", "text", "visual"],
                "description": "Retrieval mode when query is set (default hybrid).",
            },
            "visual_weight": {
                "type": "number",
                "description": "CLIP weight in hybrid fusion 0-1 (default 0.45).",
            },
            "category": {"type": "string", "enum": list(_KNOWN_CATEGORIES)},
            "exclude_trash": {"type": "boolean", "description": "Drop AI_Trash_* categories."},
            "exclude_low_quality": {
                "type": "boolean",
                "description": "Drop blur/overexposure cues and low technical / overall.",
            },
            "dedupe_burst": {"type": "boolean", "description": "Keep one best frame per near-dup / burst."},
            "sort_by": {"type": "string", "enum": list(_SORT_KEYS), "description": "Sort key (default overall)."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "description": "Max rows (default 20)."},
        },
        "additionalProperties": False,
    }

    def __init__(self, base_dir: str) -> None:
        self._base_dir = base_dir

    def run(self, args: dict[str, Any]) -> SkillResult:
        rows = _load_rows(self._base_dir)
        if not rows:
            return SkillResult(
                ok=True,
                output="No analyzed photos found in this session.",
                metadata={"rows": [], "count": 0},
            )

        sort_by = str(args.get("sort_by") or "overall")
        if sort_by not in _SORT_KEYS:
            sort_by = "overall"
        try:
            limit = int(args.get("limit") or 20)
        except (TypeError, ValueError):
            limit = 20
        limit = max(1, min(100, limit))

        filter_args = dict(args)
        filter_args["_sort_by"] = sort_by
        query = str(args.get("query") or "").strip()
        expanded = _expand_query_terms(query)
        citations: list[dict[str, Any]] = []
        rag_meta: dict[str, Any] = {}

        if query and _style_intent(query) == "slow_shutter":
            return _search_slow_shutter(rows, base_dir=self._base_dir, limit=limit)

        clip_query = ""
        clip_neg: str | None = None
        framing: str | None = None
        if query:
            clip_query, clip_neg, framing = _clip_prompts(query, expanded)
            # Score/category filters first (no text query gate), then hybrid RAG rank.
            pre_args = dict(filter_args)
            pre_args.pop("query", None)
            candidates = _filter_rows(rows, pre_args)
            text_hits = _filter_rows(rows, filter_args)
            candidates = _cap_rag_candidates(candidates, text_hits)
            from services.agent.rag import hybrid_retrieve

            mode = str(args.get("mode") or "hybrid")
            try:
                vw = float(args["visual_weight"]) if args.get("visual_weight") is not None else 0.45
            except (TypeError, ValueError):
                vw = 0.45
            # Framing intents rely on visual contrast; nudge weight up when no text tags.
            if framing and not text_hits:
                vw = max(vw, 0.65)
            ranked, citations, rag_meta = hybrid_retrieve(
                candidates,
                query=clip_query or query,
                query_terms=expanded,
                base_dir=self._base_dir,
                text_hit_score=lambda blob, terms: _query_hit_score(blob, terms),
                text_blob=_text_blob,
                mode=mode,
                visual_weight=vw,
                limit=max(limit * 3, limit),
                negative_query=clip_neg,
                framing_intent=framing,
            )
            filtered = ranked
            sort_label = "RAG fused score"
            rag_meta = {
                **rag_meta,
                "clip_query": clip_query,
                "clip_negative": clip_neg,
                "framing_intent": framing,
                "text_hit_count": len(text_hits),
            }
        else:
            filtered = _filter_rows(rows, filter_args)
            sort_label = sort_by

        filtered = _maybe_dedupe(filtered, self._base_dir, bool(args.get("dedupe_burst")))
        top = [_record(r) for r in filtered[:limit]]
        files = [str(r["file"]) for r in top if r.get("file")]
        if citations:
            by_file = {str(c.get("file") or ""): c for c in citations}
            citations = [by_file[f] for f in files if f in by_file]
        summary = f"{len(filtered)} photo(s) matched; showing top {len(top)} by {sort_label}."
        if rag_meta:
            summary += (
                f" RAG mode={rag_meta.get('mode')} visual={rag_meta.get('visual_available')} "
                f"citations={len(citations)}."
            )
        meta: dict[str, Any] = {
            "rows": top,
            "count": len(filtered),
            "files": files,
            "ui_action": "search",
            "query_terms": expanded[:24],
            "clip_query": clip_query or None,
            "citations": citations,
            "rag": rag_meta,
        }
        if not filtered:
            # Help the model explain empty results without inventing photos / fake tags.
            tag_counts: dict[str, int] = {}
            cat_counts: dict[str, int] = {}
            captions: list[str] = []
            vlm_content = 0
            for r in rows:
                cat = str(r.get("category") or "").strip()
                if cat:
                    cat_counts[cat] = cat_counts.get(cat, 0) + 1
                row_tags = [str(t).strip() for t in (r.get("tags") or []) if str(t).strip()]
                for tk in row_tags:
                    tag_counts[tk] = tag_counts.get(tk, 0) + 1
                sem_tags = [t for t in row_tags if not _is_pipeline_tag(t)]
                cap = _caption(r).strip()
                content_cap = cap and not _is_boilerplate_reason(cap)
                if sem_tags or content_cap:
                    vlm_content += 1
                if content_cap and len(captions) < 8:
                    captions.append(cap[:120])
            top_tags = sorted(tag_counts.items(), key=lambda kv: kv[1], reverse=True)[:12]
            semantic_tags = [(k, v) for k, v in top_tags if not _is_pipeline_tag(k)]
            pipeline_only = vlm_content == 0
            visual_ok = bool((rag_meta or {}).get("visual_available"))
            meta["top_tags"] = [{"tag": k, "count": v} for k, v in top_tags]
            meta["semantic_tags"] = [{"tag": k, "count": v} for k, v in semantic_tags[:12]]
            meta["categories"] = cat_counts
            meta["session_size"] = len(rows)
            meta["caption_samples"] = captions
            meta["tags_empty"] = not bool(top_tags)
            meta["pipeline_tags_only"] = pipeline_only
            meta["vlm_content_count"] = vlm_content
            meta["visual_available"] = visual_ok
            # Do not list score-band category names (AI_Best_*) as if they were content tags.
            if pipeline_only:
                summary += (
                    f" No semantic hits. Session has {len(rows)} photo(s) but almost no VLM "
                    "content tags/captions (mostly Stage2/Stage3 skip labels like "
                    f"{[t[0] for t in top_tags[:5] if _is_pipeline_tag(t[0])] or 'none'}). "
                    "Do NOT ask the user to try other keywords — text search cannot invent "
                    "鼓手/吉他手 tags. Recommend re-running Stage3/VLM on keepers"
                    + (
                        ", or install open-clip-torch so hybrid visual search can help."
                        if not visual_ok
                        else "; visual RAG was on but found no match in the scored corpus."
                    )
                )
            elif not visual_ok:
                summary += (
                    " No semantic hits for this query in tags/captions, and CLIP visual RAG "
                    "is unavailable (install open-clip-torch). "
                    f"Session has {vlm_content}/{len(rows)} photos with VLM content; "
                    f"semantic tags seen: {[t[0] for t in semantic_tags[:8]] or 'none'}."
                )
            else:
                summary += (
                    " No semantic hits for this query in tags/captions (visual RAG also "
                    f"empty). Session semantic tags: {[t[0] for t in semantic_tags[:8]]}."
                )
        return SkillResult(ok=True, output=summary, metadata=meta)


class GalleryStatsSkill:
    name = "gallery_stats"
    description = (
        "Summary statistics for the current session's analyzed photos: total count, "
        "counts per category, overall-score buckets, mean score, and the most common tags."
    )
    parameters = {"type": "object", "properties": {}, "additionalProperties": False}

    def __init__(self, base_dir: str) -> None:
        self._base_dir = base_dir

    def run(self, args: dict[str, Any]) -> SkillResult:
        rows = _load_rows(self._base_dir)
        total = len(rows)
        if total == 0:
            return SkillResult(ok=True, output="No analyzed photos found in this session.", metadata={"total": 0})

        by_category: dict[str, int] = {}
        buckets = {"0-60": 0, "60-90": 0, "90-100": 0}
        tag_counts: dict[str, int] = {}
        score_sum = 0.0
        for r in rows:
            cat = str(r.get("category") or "uncategorized")
            by_category[cat] = by_category.get(cat, 0) + 1
            s = _dim(r, "overall")
            score_sum += s
            if s >= 90:
                buckets["90-100"] += 1
            elif s >= 60:
                buckets["60-90"] += 1
            else:
                buckets["0-60"] += 1
            for t in r.get("tags") or []:
                tk = str(t)
                tag_counts[tk] = tag_counts.get(tk, 0) + 1

        top_tags = sorted(tag_counts.items(), key=lambda kv: kv[1], reverse=True)[:10]
        meta = {
            "total": total,
            "mean_overall": round(score_sum / total, 1),
            "by_category": by_category,
            "score_buckets": buckets,
            "top_tags": [{"tag": k, "count": v} for k, v in top_tags],
        }
        summary = (
            f"{total} analyzed photos; mean overall {meta['mean_overall']}. "
            f"Buckets: {buckets}. Categories: {by_category}."
        )
        return SkillResult(ok=True, output=summary, metadata=meta)


class ExplainPhotoSkill:
    name = "explain_photo"
    description = (
        "Return the full analysis for ONE photo by file name (exact, basename, or substring "
        "match): its overall + per-dimension scores, category (keep/discard bucket), tags, "
        "and the VLM caption/commentary — i.e. why it was scored the way it was."
    )
    parameters = {
        "type": "object",
        "properties": {"file": {"type": "string", "description": "Photo file name or a substring of it."}},
        "required": ["file"],
        "additionalProperties": False,
    }

    def __init__(self, base_dir: str) -> None:
        self._base_dir = base_dir

    def run(self, args: dict[str, Any]) -> SkillResult:
        query = str(args.get("file") or "").strip()
        if not query:
            return SkillResult(ok=False, error="'file' must be a non-empty string")
        rows = _load_rows(self._base_dir)
        ql = query.lower()

        exact = [r for r in rows if str(r.get("file") or "").lower() == ql]
        substr = exact or [r for r in rows if ql in str(r.get("file") or "").lower()]
        if not substr:
            return SkillResult(ok=False, error=f"no photo matching {query!r} in this session")
        if len(substr) > 1 and not exact:
            names = [r.get("file") for r in substr[:8]]
            return SkillResult(
                ok=False,
                error=f"{len(substr)} photos match {query!r}; be more specific",
                metadata={"candidates": names},
            )
        rec = _record(substr[0])
        summary = (
            f"{rec['file']}: overall {rec['overall_score']} "
            f"(E {rec['energy']} / T {rec['technical']} / C {rec['composition']}), "
            f"category {rec['category']}. {rec['caption']}"
        )
        return SkillResult(ok=True, output=summary, metadata={"photo": rec})


class GallerySelectSkill:
    name = "gallery_select"
    description = (
        "Apply a selection to the Gallery: mark the given files as liked / 初选. "
        "Use after gallery_search when the user asks to 选出 / 初选 / 标出来. "
        "Pass file names from a previous search result."
    )
    parameters = {
        "type": "object",
        "properties": {
            "files": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Catalog basenames to select (liked).",
            },
            "replace": {
                "type": "boolean",
                "description": "If true, replace current selection; else merge (default true).",
            },
        },
        "required": ["files"],
        "additionalProperties": False,
    }

    def __init__(self, base_dir: str) -> None:
        self._base_dir = base_dir

    def run(self, args: dict[str, Any]) -> SkillResult:
        from utils.gallery_curation import read_gallery_curation, write_gallery_curation

        files = [str(f).strip() for f in (args.get("files") or []) if str(f).strip()]
        if not files:
            return SkillResult(ok=False, error="'files' must be a non-empty list of basenames")

        known = {str(r.get("file") or "") for r in _load_rows(self._base_dir)}
        valid = [f for f in files if f in known]
        missing = [f for f in files if f not in known]
        if not valid:
            return SkillResult(ok=False, error="none of the files exist in this session", metadata={"missing": missing})

        replace = True if args.get("replace") is None else bool(args.get("replace"))
        existing = read_gallery_curation(self._base_dir) or {}
        prev_keys = list(existing.get("selected_keys") or [])
        keys = valid if replace else list(dict.fromkeys([*prev_keys, *valid]))

        written = write_gallery_curation(self._base_dir, selected_keys=keys)
        if written is None:
            return SkillResult(ok=False, error="failed to write gallery_curation.json")

        summary = f"已选中 {len(keys)} 张作为初选" + (f"（忽略未知 {len(missing)} 个文件名）" if missing else "") + "。"
        return SkillResult(
            ok=True,
            output=summary,
            metadata={
                "selected_keys": keys,
                "count": len(keys),
                "missing": missing,
                "ui_action": "reload_curation",
            },
        )


class ApplyFilmVibeSkill:
    name = "apply_film_vibe"
    description = (
        "Apply a film / grade vibe to the current Gallery session from a natural-language "
        "prompt (e.g. 复古胶片, Cinestill 800T, 黑白纪实). Persists session_vibe for Lab preview "
        "and export."
    )
    parameters = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "Style description in Chinese or English."},
            "clear": {"type": "boolean", "description": "If true, clear session vibe instead."},
        },
        "additionalProperties": False,
    }

    def __init__(self, base_dir: str) -> None:
        self._base_dir = base_dir

    def run(self, args: dict[str, Any]) -> SkillResult:
        from services.vibe_film_policy import resolve_vibe_from_prompt, session_vibe_payload_from_decision
        from utils.session_vibe import clear_session_vibe, read_session_vibe, write_session_vibe

        if bool(args.get("clear")):
            clear_session_vibe(self._base_dir)
            return SkillResult(
                ok=True,
                output="已清除 session vibe。",
                metadata={"ui_action": "reload_vibe", "session_vibe": None},
            )

        prompt = str(args.get("prompt") or "").strip()
        if not prompt:
            return SkillResult(ok=False, error="'prompt' is required unless clear=true")

        decision = resolve_vibe_from_prompt(prompt)
        payload = session_vibe_payload_from_decision(decision)
        written = write_session_vibe(self._base_dir, payload)
        if written is None:
            return SkillResult(ok=False, error="failed to write session_vibe.json")

        vibe = read_session_vibe(self._base_dir)
        label = (vibe or {}).get("label_zh") or decision.label_zh
        variant = (vibe or {}).get("film_variant") or decision.film_variant
        summary = (
            f"已应用风格「{label}」（{variant}）。"
            "Gallery 会自动打开套用该胶片的风格预览；若未弹出，请点工具旁的「打开风格预览」。"
        )
        return SkillResult(
            ok=True,
            output=summary,
            metadata={"ui_action": "reload_vibe", "session_vibe": vibe, "decision": decision.to_json()},
        )


class ExportSelectedSkill:
    name = "export_selected"
    description = (
        "Export currently selected (liked) Gallery photos: graded JPEG preview + RAW copy. "
        "Optionally pass an explicit file list; otherwise uses saved selection. Uses session "
        "vibe film when available."
    )
    parameters = {
        "type": "object",
        "properties": {
            "files": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional explicit basenames; default = current selection.",
            },
            "use_session_vibe": {
                "type": "boolean",
                "description": "Use persisted film vibe (default true).",
            },
        },
        "additionalProperties": False,
    }

    def __init__(self, base_dir: str) -> None:
        self._base_dir = base_dir

    def run(self, args: dict[str, Any]) -> SkillResult:
        from api.gallery_routes import ExportRequest, _export_images_impl
        from utils.gallery_curation import read_gallery_curation

        files = [str(f).strip() for f in (args.get("files") or []) if str(f).strip()]
        if not files:
            cur = read_gallery_curation(self._base_dir) or {}
            files = [str(k) for k in (cur.get("selected_keys") or []) if str(k).strip()]
        if not files:
            return SkillResult(ok=False, error="没有可导出的选中照片；请先 gallery_select")

        use_vibe = True if args.get("use_session_vibe") is None else bool(args.get("use_session_vibe"))
        import os

        prev_env = os.environ.get("LIVEHOUSE_GALLERY_PREVIEWS_DIR")
        os.environ["LIVEHOUSE_GALLERY_PREVIEWS_DIR"] = str(Path(self._base_dir).expanduser().resolve())
        try:
            req = ExportRequest(images=files, use_session_vibe=use_vibe)
            result = _export_images_impl(req)
            # FastAPI may return JSONResponse
            if hasattr(result, "body"):
                import json

                payload = json.loads(result.body.decode("utf-8"))
                status = getattr(result, "status_code", 200)
                if status >= 400 or not payload.get("success", True):
                    return SkillResult(
                        ok=False,
                        error=str(payload.get("error") or payload.get("detail") or "export failed"),
                        metadata={"export": payload},
                    )
            elif isinstance(result, dict):
                payload = result
                if payload.get("success") is False:
                    return SkillResult(
                        ok=False,
                        error=str(payload.get("error") or "export failed"),
                        metadata={"export": payload},
                    )
            else:
                payload = {"raw": str(result)}

            export_dir = payload.get("export_dir") or payload.get("path") or ""
            summary = f"已导出 {len(files)} 张（含预览 JPEG 与 RAW 副本）" + (f"：{export_dir}" if export_dir else "。")
            return SkillResult(
                ok=True,
                output=summary,
                metadata={"ui_action": "export_done", "files": files, "export": payload},
            )
        except Exception as exc:
            return SkillResult(ok=False, error=f"export failed: {exc}")
        finally:
            if prev_env is None:
                os.environ.pop("LIVEHOUSE_GALLERY_PREVIEWS_DIR", None)
            else:
                os.environ["LIVEHOUSE_GALLERY_PREVIEWS_DIR"] = prev_env


class MarkScoreGapSkill:
    name = "mark_score_gap"
    description = (
        "Find photos with high technical score but mediocre composition (or similar gaps), "
        "return them, and optionally select them in Gallery so they are highlighted."
    )
    parameters = {
        "type": "object",
        "properties": {
            "min_technical": {"type": "number", "description": "Default 7.5"},
            "max_composition": {"type": "number", "description": "Default 6.5"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            "select": {"type": "boolean", "description": "Also mark as liked selection (default true)."},
        },
        "additionalProperties": False,
    }

    def __init__(self, base_dir: str) -> None:
        self._base_dir = base_dir

    def run(self, args: dict[str, Any]) -> SkillResult:
        min_t = float(args.get("min_technical") if args.get("min_technical") is not None else 7.5)
        max_c = float(args.get("max_composition") if args.get("max_composition") is not None else 6.5)
        try:
            limit = int(args.get("limit") or 30)
        except (TypeError, ValueError):
            limit = 30
        limit = max(1, min(100, limit))
        select = True if args.get("select") is None else bool(args.get("select"))

        rows = _load_rows(self._base_dir)
        hit = [
            r
            for r in rows
            if _dim(r, "technical") >= min_t and _dim(r, "composition") <= max_c
        ]
        hit.sort(key=lambda r: (_dim(r, "technical") - _dim(r, "composition")), reverse=True)
        top = [_record(r) for r in hit[:limit]]
        files = [str(r["file"]) for r in top if r.get("file")]

        selected_keys: list[str] = []
        if select and files:
            sel = GallerySelectSkill(self._base_dir).run({"files": files, "replace": True})
            if sel.ok:
                selected_keys = list((sel.metadata or {}).get("selected_keys") or files)

        summary = (
            f"找到 {len(hit)} 张技术分≥{min_t} 且构图≤{max_c}；展示 {len(top)} 张"
            + ("，已在 Gallery 标出。" if selected_keys else "。")
        )
        return SkillResult(
            ok=True,
            output=summary,
            metadata={
                "rows": top,
                "count": len(hit),
                "files": files,
                "selected_keys": selected_keys,
                "ui_action": "reload_curation" if selected_keys else "search",
            },
        )


def gallery_registry(base_dir: str) -> SkillRegistry:
    """Registry for Gallery ChatDock: search + select + vibe + export."""
    reg = SkillRegistry()
    reg.register(GallerySearchSkill(base_dir))
    reg.register(GalleryStatsSkill(base_dir))
    reg.register(ExplainPhotoSkill(base_dir))
    reg.register(GallerySelectSkill(base_dir))
    reg.register(ApplyFilmVibeSkill(base_dir))
    reg.register(ExportSelectedSkill(base_dir))
    reg.register(MarkScoreGapSkill(base_dir))
    return reg
