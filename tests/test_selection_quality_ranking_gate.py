import json

from scripts.eval.run_selection_quality_eval import (
    _cloud_retry_rows,
    _ranking,
    _runtime_metrics,
    _selection_policy_flags,
    inspect_cloud_audit,
)


def test_ranking_only_admits_explicit_semantic_gate_pass() -> None:
    labels = [
        {"file": "pass.jpg"},
        {"file": "unknown.jpg"},
        {"file": "reject.jpg"},
        {"file": "missing.jpg"},
    ]
    predictions = [
        {
            "file": "pass.jpg",
            "overall_score": 70,
            "selection_eligible": True,
        },
        {
            "file": "unknown.jpg",
            "overall_score": 99,
            "selection_eligible": False,
        },
        {
            "file": "reject.jpg",
            "overall_score": 98,
            "selection_eligible": False,
        },
        {
            "file": "missing.jpg",
            "overall_score": 97,
            "selection_eligible": False,
        },
    ]
    report = _ranking(
        labels,
        predictions,
        {
            "packs": [
                {
                    "id": "all",
                    "files": [row["file"] for row in labels],
                }
            ]
        },
        defects={"reject.jpg"},
        acceptable={"pass.jpg"},
        global_k=4,
        pack_k=4,
    )
    assert report["global"]["selected_count"] == 1
    assert report["global"]["selected_ids"] == ["pass.jpg"]
    assert report["global"]["acceptable_ids"] == ["pass.jpg"]
    assert report["global"]["defect_count"] == 0


def test_gate_off_keeps_semantic_observations_without_blocking_ranking() -> None:
    eligible, rejected = _selection_policy_flags(
        stage1_reject=False,
        semantic_gate_status="reject",
        has_semantic_gate=True,
        semantic_gate={"status": "reject"},
        tags=set(),
        score=82,
        keep_threshold=60,
        selection_policy="off",
    )
    assert eligible is True
    assert rejected is False


def test_cloud_audit_preflight_checks_provenance_schema_and_usage(tmp_path) -> None:
    config_path = tmp_path / "cloud.yaml"
    config_path.write_text(
        """
stage3:
  route_b:
    enabled: true
    provider: openai
    model_name: qwen3-vl-plus
""",
        encoding="utf-8",
    )
    audit_path = tmp_path / "audit.jsonl"
    audit_path.write_text(
        json.dumps(
            {
                "image": "ok.jpg",
                "overall_score": 82,
                "dimensions": {f"d{i}": 8 for i in range(8)},
                "semantic_gate": {"status": "review"},
                "stage3_meta": {
                    "provider": "openai",
                    "model": "qwen3-vl-plus",
                    "outcome": "success",
                    "latency_ms": 1200,
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    report = inspect_cloud_audit(
        audit_path=audit_path,
        pipeline_config_path=config_path,
        expected_count=1,
    )

    assert report["passed"] is True
    assert report["schema_success_rate"] == 1.0
    assert report["usage_coverage"] == 1.0
    assert report["total_tokens"] == 120


def test_runtime_metrics_report_fallback_schema_tokens_and_optional_cost() -> None:
    metrics = _runtime_metrics(
        [
            {
                "score_source": "vlm",
                "cloud_attempted": True,
                "latency_ms": 100,
                "fallback_used": False,
                "schema_valid": True,
                "full_dimensions_valid": True,
                "prompt_tokens": 1_000_000,
                "completion_tokens": 500_000,
            }
        ],
        {
            "cost": {
                "input_usd_per_million_tokens": 1.0,
                "output_usd_per_million_tokens": 2.0,
            }
        },
    )

    assert metrics["fallback_count"] == 0
    assert metrics["schema_success_rate"] == 1.0
    assert metrics["usage_coverage"] == 1.0
    assert metrics["estimated_cost_usd"] == 2.0


def test_cloud_retry_rows_only_select_latest_bad_outcomes(tmp_path) -> None:
    audit_path = tmp_path / "audit.jsonl"
    rows = [
        {
            "image": "recovered.jpg",
            "overall_score": 50,
            "debug_info": {"tech_score": 40},
            "stage3_meta": {"outcome": "degraded_inference"},
        },
        {
            "image": "bad.jpg",
            "overall_score": 60,
            "debug_info": {"effective_tech_score": 77},
            "stage3_meta": {"outcome": "degraded_inference"},
        },
        {
            "image": "recovered.jpg",
            "overall_score": 80,
            "debug_info": {"tech_score": 90},
            "stage3_meta": {"outcome": "success"},
        },
    ]
    audit_path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )

    retry_rows = _cloud_retry_rows(audit_path)

    assert retry_rows == [
        {
            "file_name": "bad.jpg",
            "tech_score": 77.0,
            "fast_score": 0.0,
            "debug_info": {"effective_tech_score": 77},
        }
    ]
