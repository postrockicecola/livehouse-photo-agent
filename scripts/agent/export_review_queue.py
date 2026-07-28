#!/usr/bin/env python3
"""Export Gallery Agent conversations that look worth a human review.

Minimal closed-loop stage ① — signals (v1):
  - guardrail_triggered
  - route_near_miss  (N张 phrase but no ``routed`` on the turn)
  - tool_call_failed

Usage::

    python -m scripts.agent.export_review_queue
    python -m scripts.agent.export_review_queue --days 7 --out data/review_queue/2026-07-28.jsonl

Output rows are annotation stubs (stage ②). Fill ``issue_type`` / ``action`` /
``expected_behavior``, then run ``promote_to_fixtures``.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.agent import store
from services.agent.review_signals import annotation_stub, collect_conversation_reasons


def _parse_day(s: str) -> float:
    """Midnight UTC for ``YYYY-MM-DD`` → unix ts."""
    dt = datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return dt.timestamp()


def collect_candidates(
    *,
    since_ts: float,
    until_ts: float,
    limit_convs: int = 500,
) -> list[dict[str, Any]]:
    conn = store.store_connect()
    try:
        convs = store.list_conversations(
            conn, since_ts=since_ts, until_ts=until_ts, limit=limit_convs
        )
        out: list[dict[str, Any]] = []
        for c in convs:
            cid = int(c["id"])
            messages = store.load_messages(conn, cid, limit=80)
            events = store.load_agent_events(conn, cid, limit=200)
            reasons, flagged = collect_conversation_reasons(messages, events)
            if not reasons:
                continue
            # One annotation stub per flagged turn (keeps weekly review bite-sized).
            for turn in flagged:
                stub = annotation_stub(
                    conversation_id=cid,
                    reasons=turn.get("reasons") or reasons,
                    user_text=str(turn.get("user_text") or ""),
                    turn=turn,
                )
                stub["owner"] = c.get("owner")
                stub["session_id"] = c.get("session_id")
                stub["mode"] = c.get("mode")
                stub["conversation_updated_at"] = c.get("updated_at")
                out.append(stub)
        return out
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=7, help="Look back N days (default 7)")
    parser.add_argument("--since", type=str, default="", help="YYYY-MM-DD (UTC), overrides --days")
    parser.add_argument("--until", type=str, default="", help="YYYY-MM-DD (UTC, exclusive)")
    parser.add_argument(
        "--out",
        type=str,
        default="",
        help="Output jsonl path (default data/review_queue/<today>.jsonl)",
    )
    parser.add_argument("--limit-convs", type=int, default=500)
    args = parser.parse_args(argv)

    now = time.time()
    until_ts = _parse_day(args.until) if args.until else now + 1.0
    if args.since:
        since_ts = _parse_day(args.since)
    else:
        since_ts = now - max(1, int(args.days)) * 86400.0

    day_label = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_path = Path(args.out) if args.out else ROOT / "data" / "review_queue" / f"{day_label}.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    rows = collect_candidates(
        since_ts=since_ts, until_ts=until_ts, limit_convs=int(args.limit_convs)
    )
    with out_path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"wrote {len(rows)} candidate(s) → {out_path}")
    by: dict[str, int] = {}
    for r in rows:
        for reason in r.get("reasons") or []:
            by[str(reason)] = by.get(str(reason), 0) + 1
    if by:
        print("signals:", ", ".join(f"{k}={v}" for k, v in sorted(by.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
