"""Backward-compatible exports for the LangGraph curation runtime.

Prefer :mod:`services.agent.graph` (production) or :class:`CurationAgent`
(entrypoint). This module re-exports the same symbols used by early demos/tests.
"""
from __future__ import annotations

from services.agent.graph import (
    LANGGRAPH_MAPPING,
    compile_curation_graph,
    mapping_table,
    run_curation_graph,
)

__all__ = [
    "LANGGRAPH_MAPPING",
    "compile_curation_graph",
    "mapping_table",
    "run_curation_graph",
]
