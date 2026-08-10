"""Gallery search / stats / select / score-gap skills."""
from __future__ import annotations

from typing import Any

from services.agent.skills.base import SkillResult
from services.agent.skills.gallery_common import (
    _KNOWN_CATEGORIES,
    _SEMANTIC_MIN_SIM,
    _SORT_KEYS,
    _caption,
    _dim,
    _expand_query_terms,
    _filter_rows,
    _is_boilerplate_reason,
    _is_pipeline_tag,
    _load_rows,
    _matched_query_terms,
    _maybe_dedupe,
    _pick_why,
    _query_hit_score,
    _record,
    _search_slow_shutter,
    _style_intent,
    _text_blob,
    clip_rank_rows,
    hybrid_merge_rows,
    semantic_fallback_rows,
    semantic_hybrid_enabled,
)


_RECIPE_ENUM = (
    "shortlist",
    "social",
    "energy",
    "peak",
    "deliverable",
    "quality",
    "dedupe",
    "sort",
    "custom",
)


class GallerySearchSkill:
    name = "gallery_search"
    description = (
        "Search the current session's analyzed photos. Prefer a named recipe "
        "(social/energy/peak/deliverable/shortlist/quality/dedupe/sort) plus optional "
        "free-text query and limit. Free-text uses tag/caption synonyms hybrid-merged with "
        "CLIP text→image, then re-ranked by recipe score dims. Returns top-N with why lines."
    )
    # Narrow model-facing schema — rich filters still accepted from intent_router via run().
    parameters = {
        "type": "object",
        "properties": {
            "recipe": {
                "type": "string",
                "enum": list(_RECIPE_ENUM),
                "description": "Named shortlist recipe (preferred over raw score dials).",
            },
            "query": {
                "type": "string",
                "description": (
                    "Free-text query. Tag/caption synonyms + CLIP hybrid (not for 慢门 EXIF)."
                ),
            },
            "tag": {"type": "string", "description": "Only photos whose tags contain this substring."},
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "description": "Max rows (default from recipe or 20).",
            },
            "category": {
                "type": "string",
                "enum": list(_KNOWN_CATEGORIES),
                "description": "Optional score-band bucket: best / keep / trash.",
            },
        },
        "additionalProperties": False,
    }

    def __init__(self, base_dir: str) -> None:
        self._base_dir = base_dir

    def run(self, args: dict[str, Any]) -> SkillResult:
        from services.agent.gallery_search_defaults import load_search_recipes

        rows = _load_rows(self._base_dir)
        if not rows:
            return SkillResult(
                ok=True,
                output="No analyzed photos found in this session.",
                metadata={"rows": [], "count": 0},
            )

        # Merge named recipe defaults; explicit args (incl. router-rich filters) win.
        filter_args = dict(args or {})
        recipe_name = str(filter_args.get("recipe") or "").strip()
        recipes = load_search_recipes()
        if recipe_name and recipe_name in recipes and recipe_name != "custom":
            base = {
                k: v
                for k, v in recipes[recipe_name].items()
                if k not in ("default_limit",)
            }
            if filter_args.get("limit") is None and "default_limit" in recipes[recipe_name]:
                base["limit"] = recipes[recipe_name]["default_limit"]
            base["recipe"] = recipe_name
            if "rationale" not in filter_args and recipes[recipe_name].get("rationale"):
                base["rationale"] = recipes[recipe_name]["rationale"]
            # Caller/router overrides win over recipe defaults.
            for k, v in list(filter_args.items()):
                if v is not None:
                    base[k] = v
            filter_args = base

        sort_by = str(filter_args.get("sort_by") or "overall")
        if sort_by not in _SORT_KEYS:
            sort_by = "overall"
        ranking_weights = filter_args.get("ranking_weights")
        selection_goal = filter_args.get("selection_goal")
        try:
            limit = int(filter_args.get("limit") or 20)
        except (TypeError, ValueError):
            limit = 20
        limit = max(1, min(100, limit))

        filter_args["_sort_by"] = sort_by
        query = str(filter_args.get("query") or "").strip()
        expanded = _expand_query_terms(query)

        if query and _style_intent(query) == "slow_shutter":
            return _search_slow_shutter(rows, base_dir=self._base_dir, limit=limit)

        goal_style = (
            str(selection_goal.get("style") or "")
            if isinstance(selection_goal, dict)
            else ""
        )
        sort_label = f"{goal_style} composite" if ranking_weights and goal_style else sort_by
        recipe = str(filter_args.get("recipe") or "custom")
        rationale = str(filter_args.get("rationale") or f"按 {sort_label} 排序")
        dedupe = bool(filter_args.get("dedupe_burst"))

        # Aesthetic / category gates without the free-text clause.
        structural = dict(filter_args)
        structural.pop("query", None)
        structural["_sort_by"] = sort_by
        pool = _filter_rows(rows, structural)
        pool_files = {str(r.get("file") or "") for r in pool if str(r.get("file") or "").strip()}

        text_hits: list[dict[str, Any]] = []
        text_scores: dict[str, int] = {}
        if query:
            text_hits = _filter_rows(rows, filter_args)
            for r in text_hits:
                f = str(r.get("file") or "").strip()
                if not f:
                    continue
                text_scores[f] = _query_hit_score(_text_blob(r), expanded)

        clip_meta: dict[str, Any] = {}
        clip_sims: dict[str, float] = {}
        retrieval = "text" if query else "recipe"

        if query and semantic_hybrid_enabled():
            clip_pool = pool if pool else rows
            _ranked, clip_sims, clip_meta = clip_rank_rows(
                clip_pool,
                base_dir=self._base_dir,
                query=query,
                top_k=max(limit * 4, 32),
                min_sim=0.0,
            )
            filtered = hybrid_merge_rows(
                rows=rows,
                text_hits=text_hits,
                text_scores=text_scores,
                clip_sims=clip_sims,
                pool_files=pool_files,
                sort_by=sort_by,
                ranking_weights=ranking_weights,
                min_sim=_SEMANTIC_MIN_SIM,
            )
            if filtered:
                retrieval = "hybrid" if text_hits and clip_sims else ("clip" if clip_sims else "text")
                if retrieval == "hybrid":
                    recipe = f"{recipe}+hybrid" if recipe != "custom" else "hybrid"
                    rationale = f"{rationale}（标签/caption + CLIP 混合检索）"
                elif retrieval == "clip":
                    recipe = f"{recipe}+clip" if recipe != "custom" else "clip_text"
                    rationale = f"{rationale}（文本无命中，CLIP 语义召回）"
        elif query:
            filtered = list(text_hits)
            if not filtered:
                clip_rows, clip_meta = semantic_fallback_rows(
                    pool if pool else rows,
                    base_dir=self._base_dir,
                    query=query,
                    limit=limit,
                )
                if clip_rows:
                    filtered = clip_rows
                    retrieval = "clip"
                    recipe = f"{recipe}+clip" if recipe != "custom" else "clip_text"
                    rationale = f"{rationale}（文本无命中，改用 CLIP 语义召回）"
        else:
            filtered = list(pool)

        filtered = _maybe_dedupe(filtered, self._base_dir, dedupe)

        top_rows = filtered[:limit]
        top = []
        pick_reasons: list[dict[str, str]] = []
        for r in top_rows:
            fname = str(r.get("file") or "").strip()
            clip_sim = clip_sims.get(fname) if fname else None
            matched = _matched_query_terms(r, expanded) if expanded else []
            why = _pick_why(
                r,
                sort_by=sort_by,
                recipe=recipe,
                ranking_weights=ranking_weights,
                clip_sim=clip_sim,
                matched_terms=matched or None,
            )
            extra: dict[str, Any] = {"why": why}
            if clip_sim is not None:
                extra["clip_sim"] = round(float(clip_sim), 4)
            if matched:
                extra["matched_terms"] = matched
            rec = _record(r, extra=extra)
            top.append(rec)
            if rec.get("file"):
                pick_reasons.append({"file": str(rec["file"]), "why": why})
        files = [str(r["file"]) for r in top if r.get("file")]
        summary = (
            f"{len(filtered)} photo(s) matched; showing top {len(top)} by {sort_label}."
            f" recipe={recipe}. retrieval={retrieval}. {rationale}"
        )
        meta: dict[str, Any] = {
            "rows": top,
            "count": len(filtered),
            "files": files,
            "ui_action": "search",
            "query_terms": expanded[:24],
            "recipe": recipe,
            "rationale": rationale,
            "sort_by": sort_label,
            "pick_reasons": pick_reasons,
            "retrieval": retrieval,
        }
        if isinstance(selection_goal, dict):
            meta["selection_goal"] = selection_goal
        if isinstance(ranking_weights, dict):
            meta["ranking_weights"] = ranking_weights
        if clip_meta.get("attempted"):
            meta["semantic_hybrid" if retrieval == "hybrid" else "semantic_fallback"] = clip_meta
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
            meta["categories"] = cat_counts
            meta["session_size"] = len(rows)
            meta["vlm_content_count"] = vlm_content
            meta["pipeline_tags_only"] = pipeline_only
            # Data-layer guard: when only pipeline labels exist, do not hand the model
            # tag lists it can misquote as semantic content (AI_Best_* / stage3_skipped).
            if pipeline_only:
                meta["tag_status"] = "not_available"
                meta["tags_empty"] = True
                summary += (
                    f" No semantic hits. Session has {len(rows)} photo(s) but VLM content "
                    "tags/captions are not available (Stage2/Stage3 skip labels only). "
                    "Text search cannot match 鼓手/吉他手; re-run Stage3/VLM on keepers."
                )
            else:
                meta["tag_status"] = "available"
                meta["top_tags"] = [{"tag": k, "count": v} for k, v in top_tags]
                meta["semantic_tags"] = [{"tag": k, "count": v} for k, v in semantic_tags[:12]]
                meta["caption_samples"] = captions
                meta["tags_empty"] = not bool(top_tags)
                summary += (
                    " No semantic hits for this query in tags/captions. "
                    f"Session has {vlm_content}/{len(rows)} photos with VLM content; "
                    f"semantic tags seen: {[t[0] for t in semantic_tags[:8]] or 'none'}."
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
                "files": keys,
                "count": len(keys),
                "missing": missing,
                "ui_action": "reload_curation",
            },
        )

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

