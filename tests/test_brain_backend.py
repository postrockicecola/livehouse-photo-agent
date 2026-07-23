"""Brain backend selector (Batch E portability scaffold)."""
from __future__ import annotations

import pytest

from utils.brain_backend import (
    PostgresBrainBackend,
    SqliteBrainBackend,
    get_brain_backend,
    normalize_brain_backend_name,
)


def test_normalize_backend_names():
    assert normalize_brain_backend_name(None) == "sqlite"
    assert normalize_brain_backend_name("postgres") == "postgres"
    assert normalize_brain_backend_name("postgresql") == "postgres"


def test_sqlite_backend_connects(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMA_BRAIN_DB", str(tmp_path / "brain.db"))
    b = get_brain_backend("sqlite")
    assert isinstance(b, SqliteBrainBackend)
    assert b.dialect() == "sqlite"
    conn = b.connect()
    try:
        assert conn.execute("SELECT 1").fetchone()[0] == 1
    finally:
        conn.close()


def test_postgres_backend_refuses_connect():
    b = get_brain_backend("postgres")
    assert isinstance(b, PostgresBrainBackend)
    with pytest.raises(NotImplementedError):
        b.connect()
