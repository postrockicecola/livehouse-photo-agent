"""Regression tests for the repo defect-review security / reliability fixes."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def test_path_is_under_roots_rejects_escape(tmp_path):
    from utils.http_security import path_is_under_roots

    root = tmp_path / "gallery"
    root.mkdir()
    inside = root / "a.jpg"
    inside.write_bytes(b"x")
    outside = tmp_path / "secret.txt"
    outside.write_text("nope")
    assert path_is_under_roots(inside, [root]) is True
    assert path_is_under_roots(outside, [root]) is False


def test_assert_public_http_url_blocks_loopback():
    from utils.http_security import assert_public_http_url

    with pytest.raises(ValueError):
        assert_public_http_url("http://127.0.0.1:8080/secret")
    with pytest.raises(ValueError):
        assert_public_http_url("http://localhost/x")


def test_image_path_traversal_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVEHOUSE_ALLOW_INSECURE_LOCAL", "1")
    # Outside any gallery/session root (session parent is intentionally allowlisted).
    outside_root = tmp_path / "elsewhere"
    outside_root.mkdir()
    secret = outside_root / "secret.txt"
    secret.write_text("top-secret")
    gallery = tmp_path / "session" / "Previews"
    gallery.mkdir(parents=True)
    (gallery / "ok.jpg").write_bytes(b"\xff\xd8\xff\xd9")

    from gallery_server import app
    from api import gallery_routes

    monkeypatch.setattr(gallery_routes, "BASE_DIR", str(gallery))
    monkeypatch.setattr(gallery_routes, "_runtime_base_dir", lambda: str(gallery))

    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/image", params={"path": str(secret)})
    assert r.status_code in (403, 404)
    r_etc = client.get("/image", params={"path": "/etc/passwd"})
    assert r_etc.status_code in (403, 404)


def test_previews_dir_has_images_skips_non_image_first_entry(tmp_path):
    from api.gallery_routes import _previews_dir_has_images

    root = tmp_path / "Previews"
    sub = root / "AI_Keep_60-90"
    sub.mkdir(parents=True)
    (sub / ".DS_Store").write_bytes(b"x")
    (sub / "shot.jpg").write_bytes(b"y")
    assert _previews_dir_has_images(str(root)) is True


def test_stage3_cache_evicts_at_max_entries():
    from services.cache.stage3_cache import Stage3PHashCache

    cache = Stage3PHashCache(max_hamming=0, max_entries=3)
    for i in range(5):
        cache.store_result(i + 1, {"score": float(i), "stage3_meta": {"outcome": "ok"}})
    assert cache.metrics_dict()["stage3_vlm_cache_entries"] == 3
    assert cache.metrics_dict()["stage3_vlm_cache_evictions"] >= 2


def test_infra_mutating_requires_ops_token_off_loopback(monkeypatch):
    monkeypatch.setenv("LIVEHOUSE_OPS_TOKEN", "secret-ops")
    monkeypatch.delenv("LIVEHOUSE_ALLOW_INSECURE_LOCAL", raising=False)
    from gallery_server import app

    client = TestClient(app, raise_server_exceptions=False)
    r = client.post("/api/infra/jobs/1/cancel")
    assert r.status_code == 401
    r2 = client.post(
        "/api/infra/jobs/1/cancel",
        headers={"X-Livehouse-Ops-Token": "secret-ops"},
    )
    # 404 job missing is fine — auth passed.
    assert r2.status_code in (404, 400, 200)


def test_cors_origins_not_wildcard():
    from utils.http_security import cors_allow_origins

    origins = cors_allow_origins()
    assert "*" not in origins
    assert any("3000" in o for o in origins)


def test_assert_public_http_url_blocks_loopback():
    from utils.http_security import assert_public_http_url

    with pytest.raises(ValueError, match="not allowed|non-public"):
        assert_public_http_url("http://127.0.0.1/admin")
