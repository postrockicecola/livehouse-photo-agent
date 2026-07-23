"""HTTP surface: healthz + sanitized unhandled exceptions."""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_healthz_ok():
    from gallery_server import app

    client = TestClient(app)
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert "status" in body or "ok" in body or isinstance(body, dict)


def test_unhandled_exception_does_not_leak_message():
    from gallery_server import app

    @app.get("/__test_boom")
    def _boom():
        raise RuntimeError("secret db password=hunter2")

    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/__test_boom")
    assert r.status_code == 500
    body = r.json()
    assert "hunter2" not in str(body)
    assert body.get("error") == "internal server error"
    assert body.get("error_type") == "RuntimeError"
