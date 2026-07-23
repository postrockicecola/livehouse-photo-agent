"""
Brain SSOT backend selection — portfolio-honest portability scaffold (Batch E).

Current production path remains :func:`utils.luma_brain.brain_connect` (SQLite).
``LIVEHOUSE_BRAIN_BACKEND=postgres`` selects a stub that fails loudly so operators
cannot silently pretend a cluster DB is wired. See ``docs/PLATFORM_SCOPE.txt``.
"""
from __future__ import annotations

import os
import sqlite3
from typing import Protocol, runtime_checkable


@runtime_checkable
class BrainBackend(Protocol):
    def dialect(self) -> str: ...

    def connect(self) -> sqlite3.Connection: ...


class SqliteBrainBackend:
    """Thin wrapper over the existing SQLite ledger."""

    def dialect(self) -> str:
        return "sqlite"

    def connect(self) -> sqlite3.Connection:
        from utils.luma_brain import brain_connect

        return brain_connect()


class PostgresBrainBackend:
    """
    Explicit non-implementation.

    Multi-writer / multi-node SSOT belongs here later (connection pool + migrations).
    Until then, refuse connect so demos stay honest.
    """

    def dialect(self) -> str:
        return "postgres"

    def connect(self) -> sqlite3.Connection:
        raise NotImplementedError(
            "Postgres brain backend is not implemented. "
            "SQLite remains the execution SSOT (single-node). "
            "Set LIVEHOUSE_BRAIN_BACKEND=sqlite or unset it. "
            "See docs/PLATFORM_SCOPE.txt."
        )


def normalize_brain_backend_name(raw: str | None) -> str:
    s = str(raw or "").strip().lower()
    if not s:
        return "sqlite"
    if s in {"postgres", "postgresql", "pg"}:
        return "postgres"
    if s in {"sqlite", "sqlite3"}:
        return "sqlite"
    return s


def get_brain_backend(name: str | None = None) -> BrainBackend:
    kind = normalize_brain_backend_name(
        name if name is not None else os.environ.get("LIVEHOUSE_BRAIN_BACKEND")
    )
    if kind == "postgres":
        return PostgresBrainBackend()
    if kind == "sqlite":
        return SqliteBrainBackend()
    raise ValueError(
        f"Unknown LIVEHOUSE_BRAIN_BACKEND={kind!r}; expected sqlite or postgres"
    )
