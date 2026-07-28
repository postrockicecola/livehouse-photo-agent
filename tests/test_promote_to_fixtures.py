"""promote_to_fixtures: annotated review rows → router_cases.jsonl."""
from __future__ import annotations

import json
from pathlib import Path

from scripts.agent.promote_to_fixtures import promote, row_to_router_case


def test_row_to_router_case_null_rule() -> None:
    row = {
        "action": "add_regression_test",
        "user_text": "不要按分数排序",
        "conversation_id": 9,
        "expected_behavior": {"should_route": False, "rule_id": "sort_overall"},
        "issue_type": "negation_missed",
        "reviewed_at": "2026-07-28",
    }
    case = row_to_router_case(row, added="2026-07-28")
    assert case is not None
    assert case["expected_rule_id"] is None
    assert case["input"] == "不要按分数排序"


def test_promote_dedupes(tmp_path: Path) -> None:
    review = tmp_path / "review.jsonl"
    router = tmp_path / "router_cases.jsonl"
    review.write_text(
        json.dumps(
            {
                "action": "add_regression_test",
                "user_text": "帮我选20张照片",
                "conversation_id": 1,
                "expected_behavior": {
                    "should_route": True,
                    "rule_id": "shortlist_select",
                    "expected_args": {"limit": 20},
                },
                "issue_type": "missed_route",
                "reviewed_at": "2026-07-28",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    # Seed existing duplicate
    router.write_text(
        json.dumps({"input": "帮我选20张照片", "expected_rule_id": "shortlist_select"}, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    report = promote(review, router_path=router, dry_run=False)
    assert report["added"] == 0
    assert report["skipped_dup"] == 1

    review.write_text(
        json.dumps(
            {
                "action": "add_regression_test",
                "user_text": "找出15张给我",
                "conversation_id": 2,
                "expected_behavior": {
                    "should_route": True,
                    "rule_id": "shortlist_select",
                    "expected_args": {"limit": 15},
                },
                "issue_type": "missed_route",
                "reviewed_at": "2026-07-28",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    report2 = promote(review, router_path=router, dry_run=False)
    assert report2["added"] == 1
    lines = router.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
