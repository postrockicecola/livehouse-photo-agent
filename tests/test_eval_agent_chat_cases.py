"""Smoke test for the gallery chat badcase harness."""
from __future__ import annotations

from scripts.eval.eval_agent_chat_cases import evaluate


def test_chat_cases_all_pass():
    report = evaluate()
    assert report["total"] >= 4
    assert report["passed"] == report["total"], report["cases"]
    assert report["success_rate"] == 1.0
