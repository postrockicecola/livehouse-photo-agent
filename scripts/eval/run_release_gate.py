#!/usr/bin/env python3
"""Single release entry: PR smoke+validity, nightly selection/agent, optional release.

    python scripts/eval/run_release_gate.py --mode pr
    python scripts/eval/run_release_gate.py --mode nightly
    python scripts/eval/run_release_gate.py --mode release
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _run(cmd: list[str], *, cwd: Path = ROOT) -> int:
    print("+", " ".join(cmd), flush=True)
    return subprocess.call(cmd, cwd=str(cwd))


def _run_pr() -> int:
    steps = [
        [sys.executable, str(ROOT / "quality" / "validate_contracts.py")],
        [sys.executable, "-m", "quality.smoke"],
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/test_parse_validity.py",
            "tests/test_human_keep_v1.py",
            "tests/test_keep_source_agreement.py",
            "tests/test_prompt_ab.py",
            "tests/test_ollama_json_mode.py",
            "tests/test_stage3_parsers.py",
            "tests/test_quality_eval_run.py",
        ],
    ]
    for cmd in steps:
        code = _run(cmd)
        if code != 0:
            return code
    frozen = ROOT / "data" / "eval" / "selection_v1" / "frozen_manifest.json"
    if frozen.is_file():
        keep = _run([sys.executable, str(ROOT / "scripts" / "eval" / "build_human_keep_v1.py")])
        if keep != 0:
            return keep
        return _run([sys.executable, str(ROOT / "scripts" / "eval" / "build_prompt_ab_splits.py")])
    print("pr: selection_v1 not present; skip human_keep assemble")
    return 0


def _latest_selection_predictions() -> Path | None:
    candidates = [
        ROOT / "data/eval/selection_v1/normalized_images/analysis_results.json",
        ROOT / "data/eval/selection_v1/normalized_images/aesthetic_audit.jsonl",
    ]
    reports = ROOT / "reports" / "eval" / "selection_v1"
    if reports.is_dir():
        for path in sorted(reports.rglob("predictions.jsonl"), reverse=True):
            candidates.append(path)
        for path in sorted(reports.rglob("adapted_predictions.jsonl"), reverse=True):
            candidates.append(path)
    for path in candidates:
        if path.is_file() and path.stat().st_size > 0:
            return path
    return None


def _score_selection(output: Path) -> dict[str, Any] | None:
    preds = _latest_selection_predictions()
    if preds is None:
        print("nightly: no selection_v1 predictions on disk; skip score")
        return None
    if preds.suffix == ".json":
        # score() expects JSONL prediction rows
        data = json.loads(preds.read_text(encoding="utf-8"))
        rows = data.get("results") if isinstance(data, dict) else data
        if not isinstance(rows, list):
            print(f"nightly: unsupported predictions shape {preds}", file=sys.stderr)
            return None
        jsonl = output / "predictions.jsonl"
        output.mkdir(parents=True, exist_ok=True)
        with jsonl.open("w", encoding="utf-8") as handle:
            for row in rows:
                if isinstance(row, dict):
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        preds = jsonl
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "eval" / "run_selection_quality_eval.py"),
        "--config",
        "configs/eval/selection_v1.yaml",
        "score",
        "--predictions",
        str(preds),
        "--output",
        str(output),
    ]
    code = _run(cmd)
    report_path = output / "report.json"
    if not report_path.is_file():
        return {"harness_exit": code, "passed": False}
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["harness_exit"] = code
    return report


def _validity_failed(report: dict[str, Any] | None) -> bool:
    if not report:
        return False
    for row in report.get("failures") or []:
        if str(row.get("metric") or "").startswith("validity."):
            return True
    return False


def _run_agent_smoke() -> int:
    cmd = [
        sys.executable,
        str(ROOT / "agent_eval" / "run_eval.py"),
        "--case",
        "routed_quality_filter",
        "--no-baseline",
    ]
    return _run(cmd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("pr", "nightly", "release"), default="pr")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "reports" / "eval" / "release_gate",
    )
    parser.add_argument(
        "--skip-agent",
        action="store_true",
        help="nightly/release: skip live agent_eval (default on CI)",
    )
    args = parser.parse_args(argv)

    pr = _run_pr()
    if pr != 0:
        print("release_gate: PR suite FAIL")
        return pr
    if args.mode == "pr":
        print("release_gate: PR suite PASS")
        return 0

    report = _score_selection(args.output / "selection_v1")
    if _validity_failed(report):
        print("release_gate: selection validity FAIL")
        return 1

    agree = _run(
        [
            sys.executable,
            str(ROOT / "scripts" / "eval" / "eval_existing_keep_agreement.py"),
            "--out",
            str(args.output / "human_keep_v1" / "source_agreement.json"),
        ]
    )
    if agree not in {0, 2}:
        return agree

    preds = _latest_selection_predictions()
    if preds is not None:
        keep_score = _run(
            [
                sys.executable,
                str(ROOT / "scripts" / "eval" / "eval_human_keep.py"),
                "--predictions",
                str(preds if preds.suffix == ".jsonl" else args.output / "selection_v1" / "predictions.jsonl"),
                "--json",
                str(args.output / "human_keep_v1" / "selection_metrics.json"),
            ]
        )
        if keep_score != 0:
            print("release_gate: human_keep selection metrics FAIL")
            return keep_score
    else:
        print("nightly: no predictions; skip human_keep selection metrics")

    baseline_preds = ROOT / "reports/eval/selection_v1/full_vlm_baseline/predictions.jsonl"
    candidate_preds = (
        preds
        if preds is not None and preds.suffix == ".jsonl"
        else args.output / "selection_v1" / "predictions.jsonl"
    )
    if baseline_preds.is_file() and Path(candidate_preds).is_file():
        ab = _run(
            [
                sys.executable,
                str(ROOT / "scripts" / "eval" / "run_prompt_ab.py"),
                "--baseline",
                str(baseline_preds),
                "--candidate",
                str(candidate_preds),
                "--split",
                "canary",
                "--json",
                str(args.output / "prompt_ab" / "canary.json"),
            ]
        )
        if ab not in {0, 1}:
            return ab
        if ab == 1:
            print("release_gate: canary prompt A/B recorded candidate not better (not blocking nightly)")
    else:
        print("nightly: skip canary prompt A/B (need baseline + candidate jsonl)")

    skip_agent = args.skip_agent
    if report is None:
        print("release_gate: selection score skipped (no predictions)")
    elif not report.get("release_eligible"):
        print("release_gate: selection_v1 not release-eligible (recorded, not blocking nightly)")
        if args.mode == "release":
            return 1

    if not skip_agent:
        agent = _run_agent_smoke()
        if agent != 0:
            print("release_gate: agent smoke FAIL")
            return agent
    else:
        print("release_gate: agent smoke skipped")

    print(f"release_gate: {args.mode} PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
