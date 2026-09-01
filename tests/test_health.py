from __future__ import annotations


def test_broker_health_uses_fast_redis_probe(monkeypatch):
    import redis

    from infra.health import broker_health

    calls: list[object] = []

    class FakeRedis:
        def ping(self):
            calls.append("ping")
            return True

        def llen(self, queue):
            calls.append(("llen", queue))
            return 2

        def close(self):
            calls.append("close")

    def fake_from_url(url, **kwargs):
        calls.append((url, kwargs))
        return FakeRedis()

    monkeypatch.setenv("CELERY_BROKER_URL", "redis://redis:6379/0")
    monkeypatch.setattr(redis.Redis, "from_url", fake_from_url)

    assert broker_health() == {
        "ok": True,
        "celery_unavailable": False,
        "redis_error": None,
        "redis_list_len": 2,
    }
    assert calls == [
        (
            "redis://redis:6379/0",
            {"socket_connect_timeout": 1.0, "socket_timeout": 1.0},
        ),
        "ping",
        ("llen", "celery"),
        "close",
    ]


def test_broker_health_reports_connection_failure(monkeypatch):
    import redis

    from infra.health import broker_health

    def fake_from_url(url, **kwargs):
        raise OSError("redis unavailable")

    monkeypatch.setattr(redis.Redis, "from_url", fake_from_url)

    report = broker_health()

    assert report["ok"] is False
    assert report["celery_unavailable"] is True
    assert report["redis_list_len"] is None
    assert report["redis_error"] == "redis unavailable"
