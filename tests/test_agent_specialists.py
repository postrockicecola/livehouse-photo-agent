"""Specialist allowlists and assignment (retrieve / select / style)."""
from __future__ import annotations

from services.agent.specialists import (
    SPECIALIST_CHECKLIST,
    assign_specialist,
    filter_tool_specs,
    specialist_allows,
)


def test_specialist_checklist_ids_unique() -> None:
    ids = [row["id"] for row in SPECIALIST_CHECKLIST]
    assert ids == ["retrieve", "select", "style", "general"]
    assert len(set(ids)) == 4


def test_assign_specialist_from_text() -> None:
    assert assign_specialist("找鼓手特写") == "retrieve"
    assert assign_specialist("这场交30张给客户") == "select"
    assert assign_specialist("修成复古胶片风格") == "style"
    assert assign_specialist("你能做什么") == "general"


def test_assign_specialist_from_planned_route() -> None:
    assert (
        assign_specialist(
            "ignored",
            planned_route={"rule_id": "async_curation_job", "select_after_search": False},
        )
        == "select"
    )
    assert (
        assign_specialist(
            "ignored",
            planned_route={"rule_id": "apply_film_vibe", "calls": [{"tool": "apply_film_vibe"}]},
        )
        == "style"
    )


def test_specialist_allows() -> None:
    assert specialist_allows("retrieve", "gallery_search")
    assert not specialist_allows("retrieve", "export_selected")
    assert not specialist_allows("retrieve", "gallery_select")
    assert specialist_allows("select", "submit_curation_job")
    assert not specialist_allows("select", "apply_film_vibe")
    assert specialist_allows("style", "export_selected")
    assert specialist_allows("general", "export_selected")
    assert specialist_allows("retrieve", "write_artifact")
    assert not specialist_allows("style", "write_artifact")


def test_filter_tool_specs_by_specialist() -> None:
    specs = [
        {"type": "function", "function": {"name": "gallery_search", "parameters": {}}},
        {"type": "function", "function": {"name": "export_selected", "parameters": {}}},
    ]
    filtered = filter_tool_specs(specs, "retrieve")
    names = [s["function"]["name"] for s in filtered]
    assert names == ["gallery_search"]
    assert len(filter_tool_specs(specs, "general")) == 2
