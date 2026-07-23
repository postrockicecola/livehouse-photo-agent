"""Staged DAG dispatch: STAGE2 and STAGE3 are separate executor branches."""
from __future__ import annotations

import ast
from pathlib import Path


def _stage_branch_calls(source: str, stage_name: str) -> list[str]:
    """Return attribute names called on ``runner`` inside the matching elif branch."""
    tree = ast.parse(source)
    calls: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if not (
            isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Name)
            and test.left.id == "stage_name"
            and any(
                isinstance(c, ast.Constant) and c.value == stage_name for c in test.comparators
            )
        ):
            continue
        for stmt in node.body:
            for sub in ast.walk(stmt):
                if (
                    isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Attribute)
                    and isinstance(sub.func.value, ast.Name)
                    and sub.func.value.id == "runner"
                ):
                    calls.append(sub.func.attr)
    return calls


def test_stage2_does_not_call_stage3():
    src = Path("services/job_executor.py").read_text(encoding="utf-8")
    stage2 = _stage_branch_calls(src, "STAGE2_FAST_SCORE")
    stage3 = _stage_branch_calls(src, "STAGE3_VLM")
    assert stage2 == ["run_stage2_fast_score"]
    assert stage3 == ["run_stage3_vlm"]
    assert "run_stage3_vlm" not in stage2


def test_dispatch_next_uses_plan_dispatch_helper():
    src = Path("services/job_executor.py").read_text(encoding="utf-8")
    assert "send_run_jobs_for_ids" in src
    assert 'chain_policy="plan_dispatch"' in src or "chain_policy='plan_dispatch'" in src
