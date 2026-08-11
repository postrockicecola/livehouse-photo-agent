"""Gallery skills: search/select/vibe/export for the ChatDock + landing hero prompts.

Search remains grounded in ``analysis_results.json``. Select / vibe / export write through
the same persistence paths the Gallery UI uses (curation JSON, session vibe, export API).

This module is a thin façade that re-exports the skill registry and symbols imported by
tests / eval scripts.
"""
from __future__ import annotations

from services.agent.skills.archive_search import ArchiveSearchSkill
from services.agent.skills.base import SkillRegistry
from services.agent.skills.gallery_common import (
    _QUERY_SYNONYMS,
    _expand_query_terms,
    _is_boilerplate_reason,
    _is_pipeline_tag,
    _load_exposure_times,
    _load_rows,
    _normalize_category,
    _query_hit_score,
    _style_intent,
    _text_blob,
    load_query_synonyms,
)
from services.agent.skills.gallery_export import ExportSelectedSkill
from services.agent.skills.gallery_film import ApplyFilmVibeSkill, RecommendFilmForPhotoSkill
from services.agent.skills.gallery_search import (
    ExplainPhotoSkill,
    GallerySearchSkill,
    GallerySelectSkill,
    GalleryStatsSkill,
    MarkScoreGapSkill,
)

__all__ = [
    "ApplyFilmVibeSkill",
    "ArchiveSearchSkill",
    "ExplainPhotoSkill",
    "ExportSelectedSkill",
    "GallerySearchSkill",
    "GallerySelectSkill",
    "GalleryStatsSkill",
    "MarkScoreGapSkill",
    "RecommendFilmForPhotoSkill",
    "_QUERY_SYNONYMS",
    "_expand_query_terms",
    "_is_boilerplate_reason",
    "_is_pipeline_tag",
    "_load_exposure_times",
    "_load_rows",
    "_normalize_category",
    "_query_hit_score",
    "_style_intent",
    "_text_blob",
    "gallery_registry",
    "load_query_synonyms",
]


def gallery_registry(base_dir: str) -> SkillRegistry:
    """Registry for Gallery ChatDock: search + select + vibe + export."""
    reg = SkillRegistry()
    reg.register(ArchiveSearchSkill(base_dir))
    reg.register(GallerySearchSkill(base_dir))
    reg.register(GalleryStatsSkill(base_dir))
    reg.register(ExplainPhotoSkill(base_dir))
    reg.register(GallerySelectSkill(base_dir))
    reg.register(RecommendFilmForPhotoSkill(base_dir))
    reg.register(ApplyFilmVibeSkill(base_dir))
    reg.register(ExportSelectedSkill(base_dir))
    reg.register(MarkScoreGapSkill(base_dir))
    return reg
