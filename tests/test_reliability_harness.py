"""Schema checks for ``reliability_harness`` report documents (no DB)."""
from __future__ import annotations

from reliability_harness import SCHEMA_VERSION, build_report_document, write_reports_to_dir
from reliability_scenarios import scenario_malformed_model_json_parse_safe


def test_build_report_document_includes_summary_and_scenarios():
    r = scenario_malformed_model_json_parse_safe()
    doc = build_report_document([r])
    assert doc["schema_version"] == SCHEMA_VERSION
    assert doc["summary"]["total"] == 1
    assert doc["summary"]["passed"] >= 0
    assert len(doc["scenarios"]) == 1
    row = doc["scenarios"][0]
    assert "assertions" in row
    assert "metrics" in row
    assert row["id"] == r.id


def test_write_reports_to_dir_writes_json_and_md(tmp_path):
    r = scenario_malformed_model_json_parse_safe()
    paths = write_reports_to_dir(tmp_path, [r])
    assert paths["json"].is_file()
    assert paths["markdown"].is_file()
    assert paths["json"].read_text(encoding="utf-8").strip().startswith("{")
