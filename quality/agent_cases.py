"""Load / validate Gallery agent L0 cases and router paraphrase tables."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Optional

_REPO = Path(__file__).resolve().parents[1]
_DEFAULT_CASES = _REPO / "data" / "eval" / "agent" / "cases.v1.jsonl"
_DEFAULT_PARAPHRASES = _REPO / "data" / "eval" / "agent" / "router_paraphrases.v1.jsonl"
_SESSIONS = _REPO / "data" / "eval" / "agent" / "sessions"

_CASE_SPLITS = frozenset({"smoke", "core", "regression", "hard"})
_CASE_TAGS = frozenset(
    {
        "routed",
        "semantic",
        "memory",
        "vibe",
        "export",
        "control",
        "negation",
        "compound",
        "search",
        "hard",
        "smoke",
    }
)


def agent_eval_root() -> Path:
    return _REPO / "data" / "eval" / "agent"


def session_dir(session: str = "smoke") -> Path:
    return _SESSIONS / (session or "smoke")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        obj = json.loads(line)
        if not isinstance(obj, dict):
            raise ValueError(f"{path}:{i}: expected object")
        rows.append(obj)
    return rows


def validate_agent_chat_case(doc: dict[str, Any], path: str = "case") -> list[str]:
    errors: list[str] = []
    if doc.get("schema_version") != "agent_chat_case.v1":
        errors.append(f"{path}: schema_version must be agent_chat_case.v1")
    for key in ("id", "utterance", "split", "expect"):
        if key not in doc:
            errors.append(f"{path}: missing required field '{key}'")
    if not isinstance(doc.get("id"), str) or not str(doc.get("id") or "").strip():
        errors.append(f"{path}: id must be non-empty string")
    if not isinstance(doc.get("utterance"), str) or not str(doc.get("utterance") or "").strip():
        errors.append(f"{path}: utterance must be non-empty string")
    split = doc.get("split")
    if split not in _CASE_SPLITS:
        errors.append(f"{path}: split must be one of {sorted(_CASE_SPLITS)}")
    expect = doc.get("expect")
    if not isinstance(expect, dict):
        errors.append(f"{path}: expect must be object")
    else:
        if "route" in expect and expect["route"] is not None and not isinstance(expect["route"], str):
            errors.append(f"{path}: expect.route must be string or null")
        tools = expect.get("tools")
        if tools is not None and (
            not isinstance(tools, list) or not all(isinstance(t, str) and t for t in tools)
        ):
            errors.append(f"{path}: expect.tools must be string[]")
    tags = doc.get("tags")
    if tags is not None:
        if not isinstance(tags, list):
            errors.append(f"{path}: tags must be array")
        else:
            bad = [t for t in tags if t not in _CASE_TAGS]
            if bad:
                errors.append(f"{path}: invalid tags: {bad}")
    paraphrases = doc.get("paraphrases")
    if paraphrases is not None and (
        not isinstance(paraphrases, list) or not all(isinstance(p, str) and p.strip() for p in paraphrases)
    ):
        errors.append(f"{path}: paraphrases must be non-empty strings")
    mq = doc.get("model_queue")
    if mq is not None and (not isinstance(mq, list) or not all(isinstance(x, str) for x in mq)):
        errors.append(f"{path}: model_queue must be string[]")
    session = doc.get("session") or "smoke"
    if not session_dir(str(session)).is_dir():
        errors.append(f"{path}: missing session fixture sessions/{session}/")
    return errors


def validate_router_paraphrase(doc: dict[str, Any], path: str = "row") -> list[str]:
    errors: list[str] = []
    if doc.get("schema_version") != "agent_router_paraphrase.v1":
        errors.append(f"{path}: schema_version must be agent_router_paraphrase.v1")
    for key in ("id", "utterance", "expect_rule", "rule_family"):
        if key not in doc:
            errors.append(f"{path}: missing '{key}'")
    if not isinstance(doc.get("utterance"), str) or not doc.get("utterance", "").strip():
        errors.append(f"{path}: utterance must be non-empty")
    er = doc.get("expect_rule")
    if er is not None and not isinstance(er, str):
        errors.append(f"{path}: expect_rule must be string or null")
    pol = doc.get("polarity")
    if pol not in ("positive", "negative"):
        errors.append(f"{path}: polarity must be positive|negative")
    return errors


def load_agent_cases(
    path: Optional[Path] = None,
    *,
    splits: Optional[Iterable[str]] = None,
) -> list[dict[str, Any]]:
    p = path or _DEFAULT_CASES
    rows = load_jsonl(p)
    want = frozenset(splits) if splits is not None else None
    out: list[dict[str, Any]] = []
    for i, row in enumerate(rows):
        errs = validate_agent_chat_case(row, f"{p.name}:{i + 1}")
        if errs:
            raise ValueError("; ".join(errs))
        if want is not None and row.get("split") not in want:
            continue
        out.append(row)
    return out


def load_router_paraphrases(path: Optional[Path] = None) -> list[dict[str, Any]]:
    p = path or _DEFAULT_PARAPHRASES
    rows = load_jsonl(p)
    for i, row in enumerate(rows):
        errs = validate_router_paraphrase(row, f"{p.name}:{i + 1}")
        if errs:
            raise ValueError("; ".join(errs))
    return rows
