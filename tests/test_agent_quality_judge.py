"""Phase-6 rubric + groundedness-gated judge."""
from __future__ import annotations

import json

from scripts.eval.eval_agent_judge import score_human_jsonl
from services.agent.quality_judge import (
    apply_groundedness_gate,
    human_rating,
    judge_turn,
    parse_judge_json,
    rating_to_review_stub,
    RubricScores,
)


def test_parse_judge_json() -> None:
    scores, rationale = parse_judge_json(
        '```json\n{"useful":5,"honest":4,"concise":3,"rationale":"ok"}\n```'
    )
    assert scores.useful == 5
    assert scores.honest == 4
    assert scores.concise == 3
    assert rationale == "ok"


def test_groundedness_gate_forces_fail() -> None:
    scores = RubricScores(useful=5, honest=5, concise=5)
    final, passed, gated = apply_groundedness_gate(scores, grounded_ok=False)
    assert gated
    assert not passed
    assert final.honest == 1


def test_judge_turn_gates_hallucinated_file() -> None:
    def chat_fn(_messages):
        return json.dumps(
            {"useful": 5, "honest": 5, "concise": 5, "rationale": "looks great"},
            ensure_ascii=False,
        )

    verdict = judge_turn(
        chat_fn,
        utterance="找鼓手",
        reply="推荐 ghost.jpg",
        tool_calls=[{"tool": "gallery_search", "ok": True, "metadata": {"files": ["drum_01.jpg"]}}],
    )
    assert verdict.gated
    assert not verdict.pass_
    assert verdict.scores.honest == 1
    assert verdict.raw_scores["honest"] == 5


def test_human_rating_same_gate() -> None:
    v = human_rating(
        {"useful": 5, "honest": 5, "concise": 5},
        reply="see fake_99.jpg",
        tool_calls=[{"metadata": {"files": ["real.jpg"]}}],
    )
    assert not v.pass_
    assert v.gated


def test_mock_judge_suite_passes(tmp_path) -> None:
    from scripts.eval.eval_agent_judge import main

    # Run CLI mock smoke — should be green with scripted generous judge + grounded replies
    assert main(["--mock", "--suite", "smoke"]) == 0


def test_rating_to_review_stub() -> None:
    stub = rating_to_review_stub(
        {
            "utterance": "找鼓手",
            "grounded_ok": False,
            "scores": {"useful": 5, "honest": 1, "concise": 4},
            "rationale": "invented file",
            "reply": "ghost.jpg",
            "rater": "llm_judge",
        }
    )
    assert stub["action"] == "add_regression_test"
    assert stub["issue_type"] == "hallucination"


def test_score_human_jsonl(tmp_path) -> None:
    p = tmp_path / "ratings.jsonl"
    p.write_text(
        json.dumps(
            {
                "case_id": "x",
                "utterance": "hi",
                "reply": "推荐 a.jpg",
                "tool_calls": [{"tool": "gallery_search", "metadata": {"files": ["a.jpg"]}}],
                "scores": {"useful": 4, "honest": 4, "concise": 4},
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    report = score_human_jsonl(p)
    assert report["passed"] == 1
