from __future__ import annotations

from services.agent import store
from services.agent.conversation import ConversationalAgent
from services.agent.selection_planner import apply_selection_experiences
from services.agent.skills.base import SkillRegistry, SkillResult
from services.agent.skills.experience import (
    RecordSelectionFeedbackSkill,
    RetrieveSelectionExperienceSkill,
)
from services.agent.skills.gallery_common import _filter_rows


def test_experience_store_is_owner_scoped(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LIVEHOUSE_AGENT_DB", str(tmp_path / "agent.db"))
    recorded = RecordSelectionFeedbackSkill(owner="user:alice").run(
        {
            "feedback": "这张太暗了，换一张",
            "decision": "rejected",
            "query": "有张力的吉他手",
            "files": ["dark.jpg"],
        }
    )
    alice = RetrieveSelectionExperienceSkill(owner="user:alice").run(
        {"query": "张力 吉他手"}
    )
    bob = RetrieveSelectionExperienceSkill(owner="user:bob").run(
        {"query": "张力 吉他手"}
    )

    assert recorded.ok
    assert recorded.metadata["reason_code"] == "too_dark"
    assert alice.metadata["experiences"][0]["files"] == ["dark.jpg"]
    assert bob.metadata["experiences"] == []


def test_experience_constraints_filter_rejected_and_dark_frames() -> None:
    args = apply_selection_experiences(
        {"query": "吉他手"},
        [
            {
                "experience_id": 1,
                "decision": "rejected",
                "reason_code": "too_dark",
                "feedback": "太暗",
                "files": ["rejected.jpg"],
            }
        ],
    )
    rows = [
        {
            "file": "rejected.jpg",
            "overall_score": 90,
            "tags": ["吉他手"],
            "dimensions": {"exposure_control": 9},
        },
        {
            "file": "dark.jpg",
            "overall_score": 90,
            "tags": ["吉他手"],
            "dimensions": {"exposure_control": 4},
        },
        {
            "file": "good.jpg",
            "overall_score": 80,
            "tags": ["吉他手"],
            "dimensions": {"exposure_control": 8},
        },
    ]

    assert [row["file"] for row in _filter_rows(rows, args)] == ["good.jpg"]


def test_planned_selection_is_augmented_before_deterministic_execution() -> None:
    captured: list[dict] = []

    class SearchSkill:
        name = "gallery_search"
        description = "test"
        parameters = {"type": "object", "properties": {}}

        def run(self, args):
            captured.append(dict(args))
            return SkillResult(ok=True, metadata={"files": []})

    registry = SkillRegistry()
    registry.register(SearchSkill())
    agent = ConversationalAgent(
        lambda _messages: "ok",
        skills=registry,
        selection_experience_loader=lambda goal, query: [
            {
                "experience_id": 9,
                "decision": "rejected",
                "reason_code": "blurry",
                "feedback": "以前这类照片太糊",
                "files": ["blur.jpg"],
            }
        ],
    )

    agent._execute_planned_route(
        {
            "rule_id": "shortlist_semantic_goal",
            "calls": [
                {
                    "tool": "gallery_search",
                    "args": {
                        "query": "吉他手",
                        "selection_goal": {
                            "subject": "吉他手",
                            "style": "tension",
                            "platform": "xiaohongshu",
                        },
                    },
                }
            ],
        }
    )

    assert captured[0]["min_technical"] == 6.0
    assert captured[0]["exclude_low_quality"] is True
    assert captured[0]["exclude_files"] == ["blur.jpg"]
    assert captured[0]["experience_context"][0]["experience_id"] == 9


def test_store_lists_recent_experiences(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LIVEHOUSE_AGENT_DB", str(tmp_path / "direct.db"))
    conn = store.store_connect()
    try:
        store.add_selection_experience(
            conn,
            owner="anon:s",
            tenant="default",
            query="鼓手",
            feedback="喜欢",
            decision="accepted",
            files=["a.jpg"],
        )
        rows = store.list_selection_experiences(
            conn,
            owner="anon:s",
            tenant="default",
        )
    finally:
        conn.close()
    assert rows[0]["decision"] == "accepted"
    assert rows[0]["files"] == ["a.jpg"]
