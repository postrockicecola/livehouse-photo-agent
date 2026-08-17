"""Shared scoring for Gallery agent L0/L1 case results."""
from __future__ import annotations

from typing import Any, Optional

from services.agent.conversation import _parse_tool_call
from services.agent.groundedness import extract_file_mentions, normalize_file_key


def collect_files(tool_calls: list[dict[str, Any]]) -> list[str]:
    files: list[str] = []
    for tc in tool_calls:
        meta = tc.get("metadata") or {}
        for key in ("files", "selected_keys"):
            vals = meta.get(key) or []
            if isinstance(vals, list):
                files.extend(str(f) for f in vals)
    seen: set[str] = set()
    out: list[str] = []
    for f in files:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out


def route_id_from_backend(backend: str, rule_id: Optional[str] = None) -> Optional[str]:
    if rule_id:
        return str(rule_id)
    b = str(backend or "")
    if b.startswith("routed:"):
        return b.split(":", 1)[1]
    return None


def score_case(
    *,
    case: dict[str, Any],
    reply: str,
    tool_calls: list[dict[str, Any]],
    working_memory: dict[str, Any],
    prefs: dict[str, str],
    backend: str,
    elapsed_ms: int,
    events: list[dict[str, Any]] | None = None,
    live: bool = False,
    rule_id: Optional[str] = None,
) -> dict[str, Any]:
    """Score one turn against ``case['expect']``.

    ``live=True`` relaxes brittle scripted checks (exact pref keys, max rounds,
    tools_ordered) so the same goldens work with a real model.
    """
    tools = [str(tc.get("tool") or "") for tc in tool_calls]
    files = collect_files(tool_calls)
    exp = case.get("expect") or {}
    got_route = route_id_from_backend(backend, rule_id)
    reasons: list[str] = []

    route_ok = True
    if "route" in exp:
        route_ok = exp.get("route") == got_route
        if not route_ok:
            reasons.append(f"route {got_route!r} != {exp.get('route')!r}")

    want_tools = list(exp.get("tools") or [])
    tool_name_hit = 1.0
    if want_tools:
        if exp.get("tools_ordered") and not live:
            ti = 0
            ordered_ok = True
            for w in want_tools:
                while ti < len(tools) and tools[ti] != w:
                    ti += 1
                if ti >= len(tools):
                    ordered_ok = False
                    reasons.append(f"tools_ordered missing {w} in {tools}")
                    break
                ti += 1
            tool_name_hit = 1.0 if ordered_ok else 0.0
        else:
            missing = [t for t in want_tools if t not in tools]
            tool_name_hit = (len(want_tools) - len(missing)) / len(want_tools)
            if missing:
                reasons.append(f"missing tools {missing}")
    elif exp.get("tools") == [] and tools:
        tool_name_hit = 0.0
        reasons.append(f"expected no tools, got {tools}")

    if exp.get("select_after") and "gallery_select" not in tools:
        reasons.append("expected gallery_select after search")
        tool_name_hit = min(tool_name_hit, 0.0)

    if "min_files" in exp and len(files) < int(exp["min_files"]):
        reasons.append(f"files {len(files)} < {exp['min_files']}")

    if exp.get("file_contains"):
        needle = str(exp["file_contains"]).lower()
        if not any(needle in str(f).lower() for f in files):
            reasons.append(f"no file matching {needle!r}")

    if "max_tool_calls" in exp and not live and len(tool_calls) > int(exp["max_tool_calls"]):
        reasons.append(f"too many tool calls: {len(tool_calls)}")

    if exp.get("pref_key"):
        if live:
            if "remember_preference" not in tools and exp["pref_key"] not in prefs:
                reasons.append("preference not saved (live)")
        elif exp["pref_key"] not in prefs:
            reasons.append(f"pref {exp['pref_key']} not saved")

    json_leak = _parse_tool_call(reply or "") is not None
    if exp.get("reply_must_not_json", True) and json_leak:
        reasons.append("reply still looks like tool JSON")

    for needle in exp.get("reply_must_not_contain") or []:
        if str(needle) and str(needle) in (reply or ""):
            reasons.append(f"reply contains forbidden {needle!r}")

    check_grounded = exp.get("grounded")
    if check_grounded is None:
        check_grounded = bool(tool_calls)
    grounded_ok = True
    if check_grounded:
        allowed = {normalize_file_key(f) for f in files}
        for f in (working_memory or {}).get("last_files") or []:
            allowed.add(normalize_file_key(str(f)))
        cited = extract_file_mentions(reply or "")
        bad = [c for c in cited if c not in allowed]
        if bad:
            grounded_ok = False
            reasons.append(f"ungrounded cites {bad}")

    # Empty-result honesty: allow_empty cases must not cite invented files (grounded)
    # and should not claim hits when files==0 (heuristic: no image basename in reply).
    empty_honest = True
    if exp.get("allow_empty") and len(files) == 0:
        if extract_file_mentions(reply or ""):
            empty_honest = False
            reasons.append("empty search but reply cites filenames")

    ok = not reasons
    return {
        "id": case["id"],
        "split": case.get("split"),
        "ok": ok,
        "pass": ok,
        "reasons": reasons,
        "tool_calls": len(tool_calls),
        "tools": tools,
        "route": got_route,
        "route_ok": route_ok if "route" in exp else None,
        "tool_name_acc": round(tool_name_hit, 4),
        "json_leak": json_leak,
        "grounded_ok": grounded_ok,
        "empty_honest": empty_honest,
        "allow_empty": bool(exp.get("allow_empty")),
        "files": files[:12],
        "elapsed_ms": elapsed_ms,
        "reply": (reply or "")[:240],
        "backend": backend,
        "grounding_events": sum(
            1 for e in (events or []) if isinstance(e, dict) and e.get("type") == "grounding_violation"
        ),
        "live": live,
    }


def aggregate_scores(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    if n == 0:
        return {
            "total": 0,
            "passed": 0,
            "pass_at_1": 0.0,
            "tool_name_acc": 0.0,
            "route_acc": 0.0,
            "json_leak_rate": 0.0,
            "grounded_rate": 0.0,
            "empty_honesty_rate": 0.0,
            "mean_tool_calls": 0.0,
            "p50_latency_ms": 0.0,
            "p95_latency_ms": 0.0,
        }

    def _pct(xs: list[float]) -> float:
        return round(sum(xs) / len(xs), 4) if xs else 0.0

    lat = sorted(int(r.get("elapsed_ms") or 0) for r in rows)
    route_rows = [r for r in rows if r.get("route_ok") is not None]
    empty_eval = [r for r in rows if r.get("allow_empty")]

    return {
        "total": n,
        "passed": sum(1 for r in rows if r.get("ok")),
        "pass_at_1": _pct([1.0 if r.get("ok") else 0.0 for r in rows]),
        "tool_name_acc": _pct([float(r.get("tool_name_acc") or 0.0) for r in rows]),
        "route_acc": _pct([1.0 if r.get("route_ok") else 0.0 for r in route_rows]) if route_rows else 1.0,
        "json_leak_rate": _pct([1.0 if r.get("json_leak") else 0.0 for r in rows]),
        "grounded_rate": _pct([1.0 if r.get("grounded_ok") else 0.0 for r in rows]),
        "empty_honesty_rate": _pct([1.0 if r.get("empty_honest") else 0.0 for r in empty_eval])
        if empty_eval
        else 1.0,
        "mean_tool_calls": round(sum(int(r.get("tool_calls") or 0) for r in rows) / n, 3),
        "p50_latency_ms": float(lat[len(lat) // 2]),
        "p95_latency_ms": float(lat[min(len(lat) - 1, int(round(0.95 * (len(lat) - 1))))]),
    }
