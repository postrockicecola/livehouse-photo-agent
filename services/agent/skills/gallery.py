"""Read-only Gallery skills: let the chat agent query a session's curation results.

These ground the Gallery copilot in the real ``analysis_results.json`` for a previews
directory (the same file the gallery UI reads), so tool calls return actual scores,
tags, VLM commentary and keep/discard categories — never hallucinated. All three skills
are read-only: they never export, delete, or re-run analysis.

- ``gallery_search``  — filter/sort photos (by score band, tag, category) → top-N.
- ``gallery_stats``   — counts: totals, per-category, score buckets, top tags.
- ``explain_photo``   — the full record for one file ("why was this kept/discarded?").

A registry is built per request bound to the active ``base_dir`` (previews dir).
"""
from __future__ import annotations

from typing import Any

from services.agent.skills.base import SkillRegistry, SkillResult

# Categories the pipeline writes; surfaced so the model can filter on keep/discard.
_KNOWN_CATEGORIES = ("AI_Best_90+", "AI_Keep_60-90", "AI_Trash_Below60", "best", "keep", "trash")
_SORT_KEYS = ("overall", "energy", "technical", "composition")


def _load_rows(base_dir: str) -> list[dict[str, Any]]:
    """Fresh, normalized (no disk-probe) rows from the session's analysis_results.json."""
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
        if cap:
            return str(cap)
    return str(row.get("reason") or "")


def _record(row: dict[str, Any]) -> dict[str, Any]:
    """Compact, model-friendly view of one photo."""
    return {
        "file": row.get("file"),
        "overall_score": round(float(row.get("overall_score") or 0.0), 1),
        "energy": round(float(row.get("energy") or 0.0), 1),
        "technical": round(float(row.get("technical") or 0.0), 1),
        "composition": round(float(row.get("composition") or 0.0), 1),
        "category": row.get("category"),
        "tags": row.get("tags") or [],
        "caption": _caption(row),
    }


class GallerySearchSkill:
    name = "gallery_search"
    description = (
        "Search the current session's analyzed photos. Filter by minimum/maximum overall "
        "score (0-100), a tag substring, and/or category (e.g. AI_Best_90+, AI_Keep_60-90, "
        "AI_Trash_Below60). Sort by overall|energy|technical|composition. Returns the top-N "
        "matching photos with scores, tags and caption."
    )
    parameters = {
        "type": "object",
        "properties": {
            "min_score": {"type": "number", "description": "Minimum overall score (0-100)."},
            "max_score": {"type": "number", "description": "Maximum overall score (0-100)."},
            "tag": {"type": "string", "description": "Only photos whose tags contain this substring."},
            "category": {"type": "string", "enum": list(_KNOWN_CATEGORIES)},
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
            return SkillResult(ok=True, output="No analyzed photos found in this session.",
                               metadata={"rows": [], "count": 0})

        min_score = args.get("min_score")
        max_score = args.get("max_score")
        tag = str(args.get("tag") or "").strip().lower()
        category = str(args.get("category") or "").strip()
        sort_by = str(args.get("sort_by") or "overall")
        if sort_by not in _SORT_KEYS:
            sort_by = "overall"
        try:
            limit = int(args.get("limit") or 20)
        except (TypeError, ValueError):
            limit = 20
        limit = max(1, min(100, limit))

        def _keep(row: dict[str, Any]) -> bool:
            score = float(row.get("overall_score") or 0.0)
            if min_score is not None and score < float(min_score):
                return False
            if max_score is not None and score > float(max_score):
                return False
            if category and str(row.get("category") or "") != category:
                return False
            if tag:
                tags = [str(t).lower() for t in (row.get("tags") or [])]
                if not any(tag in t for t in tags):
                    return False
            return True

        sort_field = "overall_score" if sort_by == "overall" else sort_by
        filtered = [r for r in rows if _keep(r)]
        filtered.sort(key=lambda r: float(r.get(sort_field) or 0.0), reverse=True)
        top = [_record(r) for r in filtered[:limit]]
        summary = f"{len(filtered)} photo(s) matched; showing top {len(top)} by {sort_by}."
        return SkillResult(ok=True, output=summary, metadata={"rows": top, "count": len(filtered)})


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
            return SkillResult(ok=True, output="No analyzed photos found in this session.",
                               metadata={"total": 0})

        by_category: dict[str, int] = {}
        buckets = {"0-60": 0, "60-90": 0, "90-100": 0}
        tag_counts: dict[str, int] = {}
        score_sum = 0.0
        for r in rows:
            cat = str(r.get("category") or "uncategorized")
            by_category[cat] = by_category.get(cat, 0) + 1
            s = float(r.get("overall_score") or 0.0)
            score_sum += s
            if s >= 90:
                buckets["90-100"] += 1
            elif s >= 60:
                buckets["60-90"] += 1
            else:
                buckets["0-60"] += 1
            for t in (r.get("tags") or []):
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


def gallery_registry(base_dir: str) -> SkillRegistry:
    """A registry with the three read-only gallery skills bound to ``base_dir``."""
    reg = SkillRegistry()
    reg.register(GallerySearchSkill(base_dir))
    reg.register(GalleryStatsSkill(base_dir))
    reg.register(ExplainPhotoSkill(base_dir))
    return reg
