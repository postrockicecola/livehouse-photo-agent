"""Guardrails for inference ``model_runs`` FK targets (``jobs.id``)."""

from __future__ import annotations

from utils.luma_brain import coerce_positive_job_id


def test_coerce_positive_job_id_none_and_invalid() -> None:
    assert coerce_positive_job_id(None) is None
    assert coerce_positive_job_id(0) is None
    assert coerce_positive_job_id(-1) is None
    assert coerce_positive_job_id("") is None
    assert coerce_positive_job_id("0") is None
    assert coerce_positive_job_id(True) is None
    assert coerce_positive_job_id(False) is None


def test_coerce_positive_job_id_valid() -> None:
    assert coerce_positive_job_id(42) == 42
    assert coerce_positive_job_id("99") == 99
