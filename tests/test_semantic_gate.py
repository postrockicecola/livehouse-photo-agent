import json

from PIL import Image

from services.processor.pipeline_image_ops import append_aesthetic_audit_line
from services.processor.stages.semantic_gate import (
    apply_semantic_gate_policy,
    evaluate_semantic_gate,
    sanitize_semantic_observation,
)


def _config(mode: str = "observe") -> dict:
    return {
        "stage3": {
            "semantic_gate": {
                "enabled": True,
                "mode": mode,
                "min_clear_confidence": 0.65,
                "default_min_severity": 2,
                "default_min_confidence": 0.80,
                "subjective_min_severity": 3,
                "subjective_min_confidence": 0.90,
            }
        }
    }


def test_sanitize_drops_unknown_types_and_clamps_values() -> None:
    result = sanitize_semantic_observation(
        {
            "is_present": True,
            "types": ["heavy_occlusion", "invented"],
            "severity": 9,
            "confidence": 2,
            "evidence": "Face is blocked.",
        }
    )
    assert result == {
        "is_present": True,
        "types": ["heavy_occlusion"],
        "severity": 3,
        "confidence": 1.0,
        "evidence": "Face is blocked.",
    }


def test_objective_defect_rejects_but_subjective_requires_stricter_threshold() -> None:
    objective = evaluate_semantic_gate(
        {
            "is_present": True,
            "types": ["heavy_occlusion"],
            "severity": 2,
            "confidence": 0.82,
            "evidence": "Most of the face is blocked.",
        },
        _config(),
    )
    subjective = evaluate_semantic_gate(
        {
            "is_present": True,
            "types": ["missed_moment"],
            "severity": 2,
            "confidence": 0.88,
            "evidence": "The gesture is between actions.",
        },
        _config(),
    )
    assert objective["status"] == "reject"
    assert subjective["status"] == "review"


def test_missing_gate_is_unknown_and_soft_mode_marks_unresolved() -> None:
    result = apply_semantic_gate_policy(
        {"score": 82, "tags": [], "stage3_meta": {"outcome": "success"}},
        _config("soft"),
    )
    assert result["semantic_gate"]["status"] == "unknown"
    assert "semantic_gate_unresolved" in result["tags"]


def test_observe_mode_never_adds_operational_reject_tag() -> None:
    result = apply_semantic_gate_policy(
        {
            "score": 82,
            "tags": [],
            "stage3_meta": {"outcome": "success"},
            "semantic_gate": {
                "is_present": True,
                "types": ["no_clear_subject"],
                "severity": 3,
                "confidence": 0.95,
                "evidence": "No performer is visually identifiable.",
            },
        },
        _config("observe"),
    )
    assert result["semantic_gate"]["status"] == "reject"
    assert "semantic_reject" not in result["tags"]


def test_off_mode_retains_observation_without_a_gate_decision() -> None:
    config = _config("off")
    config["stage3"]["semantic_gate"]["enabled"] = False
    result = apply_semantic_gate_policy(
        {
            "score": 82,
            "tags": [],
            "semantic_gate": {
                "is_present": True,
                "types": ["heavy_occlusion"],
                "severity": 3,
                "confidence": 0.95,
                "evidence": "The performer's face is fully blocked.",
            },
        },
        config,
    )
    assert result["semantic_gate"] == {
        "is_present": True,
        "types": ["heavy_occlusion"],
        "severity": 3,
        "confidence": 0.95,
        "evidence": "The performer's face is fully blocked.",
        "status": "disabled",
        "mode": "off",
        "policy_version": "semantic_gate.v1",
    }
    assert result["tags"] == []


def test_cloud_supplied_status_cannot_override_local_policy() -> None:
    result = evaluate_semantic_gate(
        {
            "is_present": False,
            "types": [],
            "severity": 0,
            "confidence": 0.96,
            "evidence": "",
            "status": "reject",
        },
        _config("hard"),
    )
    assert result["status"] == "pass"


def test_hard_mode_routes_high_score_semantic_reject_to_trash(tmp_path) -> None:
    source = tmp_path / "frame.jpg"
    Image.new("RGB", (8, 8)).save(source)
    folders = {
        name: tmp_path / name for name in ("best", "keep", "trash")
    }
    for folder in folders.values():
        folder.mkdir()
    config = _config("hard")
    config["stage3"]["route_b"] = {
        "enabled": True,
        "provider": "openai",
        "model_name": "qwen3-vl-plus",
    }
    config["classification"] = {
        "best_threshold": 90,
        "keep_threshold": 60,
        "selected_threshold": 70,
    }
    audit = tmp_path / "audit.jsonl"
    append_aesthetic_audit_line(
        config=config,
        folders=folders,
        log_paths={"log_file": audit},
        file_lock=None,
        image_path=str(source),
        ai_data={
            "score": 96,
            "tags": [],
            "stage3_meta": {"outcome": "success"},
            "semantic_gate": {
                "is_present": True,
                "types": ["heavy_occlusion"],
                "severity": 3,
                "confidence": 0.95,
                "evidence": "The performer's face is fully blocked.",
            },
        },
    )
    row = json.loads(audit.read_text(encoding="utf-8"))
    assert (folders["trash"] / source.name).is_file()
    assert row["overall_score"] == 96.0
    assert row["semantic_gate"]["status"] == "reject"
    assert row["stage3_meta"]["provider"] == "openai"
    assert row["stage3_meta"]["model"] == "qwen3-vl-plus"
    assert "semantic_reject" in row["tags"]
