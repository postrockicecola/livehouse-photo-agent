"""L0 agent eval assets: contract load + harness / paraphrase gates."""
from __future__ import annotations

from quality.agent_cases import load_agent_cases, load_router_paraphrases, validate_agent_chat_case
from quality.validate_contracts import validate_document
from scripts.eval.eval_agent_chat_cases import evaluate as eval_chat_cases
from scripts.eval.eval_agent_router_paraphrases import evaluate as eval_router


def test_example_agent_chat_case_contract() -> None:
    from pathlib import Path
    import json

    path = Path(__file__).resolve().parents[1] / "quality/schemas/examples/agent_chat_case.example.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert validate_document(doc, str(path)) == []
    assert validate_agent_chat_case(doc) == []


def test_load_all_agent_cases() -> None:
    cases = load_agent_cases()
    assert len(cases) >= 25
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids))


def test_load_router_paraphrases_coverage() -> None:
    rows = load_router_paraphrases()
    assert len(rows) >= 40
    families: dict[str, list[str]] = {}
    for r in rows:
        families.setdefault(str(r["rule_family"]), []).append(str(r["polarity"]))
    for family in (
        "shortlist_select",
        "shortlist_social",
        "shortlist_energy",
        "shortlist_peak",
        "shortlist_deliverable",
        "dedupe_burst",
        "exclude_low_quality",
        "sort_overall",
    ):
        pols = families.get(family) or []
        assert pols.count("positive") >= 5, family


def test_eval_agent_chat_cases_all_pass() -> None:
    report = eval_chat_cases()
    assert report["passed"] == report["total"], [
        c for c in report["cases"] if not c["ok"]
    ]


def test_eval_router_paraphrases_all_pass() -> None:
    report = eval_router()
    assert report["passed"] == report["total"], [
        c for c in report["cases"] if not c["ok"]
    ]
    assert report["micro"]["f1"] >= 0.99


def test_eval_agent_live_mock_core_gate() -> None:
    from scripts.eval.eval_agent_live import evaluate

    report = evaluate(suite="core", mode="mock")
    assert report["metrics"]["passed"] == report["metrics"]["total"], [
        c for c in report["cases"] if not c["ok"]
    ]


def test_eval_agent_judge_mock_smoke_gate() -> None:
    from scripts.eval.eval_agent_judge import main

    assert main(["--mock", "--suite", "smoke"]) == 0
