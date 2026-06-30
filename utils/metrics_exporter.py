from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


def export_metrics_json(payload: Mapping[str, Any], output_path: Path) -> Path:
    """Export structured metrics for external systems."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return output_path


def export_prometheus_metrics_placeholder(output_path: Path, metrics: Mapping[str, Any]) -> Path:
    """Write a simple Prometheus-compatible placeholder payload."""
    lines = [
        "# HELP livehouse_pipeline_total Total files observed by pipeline",
        "# TYPE livehouse_pipeline_total gauge",
        f"livehouse_pipeline_total {int(metrics.get('total', 0) or 0)}",
        "# HELP livehouse_pipeline_processed Total files processed successfully",
        "# TYPE livehouse_pipeline_processed gauge",
        f"livehouse_pipeline_processed {int(metrics.get('processed', 0) or 0)}",
        "# HELP livehouse_pipeline_failed Total files failed",
        "# TYPE livehouse_pipeline_failed gauge",
        f"livehouse_pipeline_failed {int(metrics.get('failed', 0) or 0)}",
        "# HELP livehouse_pipeline_time_cost_seconds Pipeline execution time cost",
        "# TYPE livehouse_pipeline_time_cost_seconds gauge",
        f"livehouse_pipeline_time_cost_seconds {float(metrics.get('time_cost', 0.0) or 0.0)}",
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path
