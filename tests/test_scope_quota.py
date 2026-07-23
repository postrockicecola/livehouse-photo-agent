"""Per-scope VLM quota scaffold."""
from __future__ import annotations

from infra.scope_quota import admit_vlm_for_scope, scope_quota_snapshot


def test_quota_disabled_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMA_BRAIN_DB", str(tmp_path / "brain.db"))
    monkeypatch.delenv("LIVEHOUSE_SCOPE_VLM_QUOTA_PER_HOUR", raising=False)
    gate = admit_vlm_for_scope(namespace="demo", project_key="a")
    assert gate["ok"] is True
    assert gate["enforced"] is False


def test_quota_enforced_when_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMA_BRAIN_DB", str(tmp_path / "brain.db"))
    monkeypatch.setenv("LIVEHOUSE_SCOPE_VLM_QUOTA_PER_HOUR", "2")
    assert admit_vlm_for_scope(namespace="demo", project_key="a")["ok"] is True
    assert admit_vlm_for_scope(namespace="demo", project_key="a")["ok"] is True
    denied = admit_vlm_for_scope(namespace="demo", project_key="a")
    assert denied["ok"] is False
    assert denied["error"] == "scope_vlm_quota_exceeded"
    # Other scope still has budget.
    assert admit_vlm_for_scope(namespace="demo", project_key="b")["ok"] is True
    snap = scope_quota_snapshot(namespace="demo", project_key="a")
    assert snap["enforced"] is True
    assert snap["used"] == 2
    assert snap["remaining"] == 0
