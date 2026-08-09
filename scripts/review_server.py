#!/usr/bin/env python3
"""Review UI for the Qwen-VL relabel pass -- confirm or correct AI suggestions.

Standard-library only (``http.server``), same shape as ``scripts/label_server.py``
but built around correction rather than blank-slate labeling: every frame arrives
pre-filled with the eight suggested dimensions, so the common action is one
keystroke and the reviewer only spends attention where the model is wrong.

``overall`` is never typed by hand. It is recomputed from the eight dimensions
using the weights in ``scripts/eval/relabel_qwen.py``, so the reviewer argues
about concrete axes ("focus is a 4, not a 7") instead of an abstract total, and
the resulting distribution cannot collapse back onto 70/75/80.

Two files are written, deliberately separated:

* ``data/eval/labels.jsonl`` -- clean eval schema, the registered dataset itself
* ``review_log.jsonl`` -- append-only audit: who/when, accepted vs edited, the AI
  value at review time, and whether the AI was hidden (blind frames)

Run::

    python scripts/review_server.py --suggestions data/eval/relabel/qwen_suggestions.jsonl

Then open http://127.0.0.1:8901 . Saves on navigation; resume any time.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.eval.labels import DIM_KEYS, load_labels, normalize_name
from scripts.eval.relabel_qwen import (
    DEFAULT_KEEP_TOP_PCT,
    DIM_WEIGHTS,
    GATE_CAP,
    GATE_DIMS,
    GATE_THRESHOLD,
    derive_overall,
    normalize_weights,
    rank_keep_map,
    read_jsonl,
)
from scripts.label_server import _CONTENT_TYPES, LabelStore, _index_images
from utils.stage3_dimensions import STAGE3_DIM_LABELS

DIM_META = [
    {"key": k, "label": STAGE3_DIM_LABELS.get(k, k), "weight": round(w, 4)}
    for k, w in normalize_weights(DIM_WEIGHTS).items()
]

_append_lock = threading.Lock()


def _clamp(v: Any, lo: float, hi: float) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return max(lo, min(hi, x))


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    line = json.dumps(record, ensure_ascii=False)
    with _append_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())


def _sanitize_review(body: dict[str, Any], weights: dict[str, float]) -> dict[str, Any]:
    """Validate a submitted review and recompute ``overall`` server-side."""
    file = str(body.get("file") or "").strip()
    if not file:
        raise ValueError("missing file")
    dims_in = body.get("dims") or {}
    if not isinstance(dims_in, dict):
        raise ValueError("dims must be an object")
    dims: dict[str, float] = {}
    for k in DIM_KEYS:
        v = _clamp(dims_in.get(k), 0, 10)
        if v is None:
            raise ValueError(f"dimension {k} is required (0-10)")
        dims[k] = round(v, 1)
    overall, gate = derive_overall(dims, weights)
    keep = body.get("keep")
    return {
        "file": Path(file).name,
        "overall": overall,
        "dims": dims,
        "keep": bool(keep) if isinstance(keep, bool) else None,
        "notes": str(body.get("notes") or ""),
        "_gate": gate,
    }


class Handler(BaseHTTPRequestHandler):
    images_dir: Path
    store: LabelStore
    log_path: Path
    suggestions: dict[str, dict[str, Any]]
    prior: dict[str, dict[str, Any]]
    blind: set[str]
    image_index: dict[str, Path]
    reviewer: str
    weights: dict[str, float]
    keep_rank: dict[str, bool]
    keep_threshold: float | None
    keep_top_pct: float
    keep_prefill: bool

    server_version = "ReviewServer/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("[review] " + (fmt % args) + "\n")

    # --- helpers ---
    def _send_json(self, obj: Any, status: int = 200) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_bytes(self, data: bytes, ctype: str, cache: str = "public, max-age=300") -> None:
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(data)

    def _build_queue(self) -> dict[str, Any]:
        reviewed = self.store.as_map()
        items: list[dict[str, Any]] = []
        accepted = edited = 0
        for key in sorted(self.suggestions.keys()):
            sug = self.suggestions[key]
            disp = str(sug.get("file") or key)
            done = reviewed.get(key)
            origin = None
            if done is not None:
                origin = self._classify(done, sug, self.keep_rank.get(key))
                if origin == "ai_accepted":
                    accepted += 1
                else:
                    edited += 1
            prior = self.prior.get(key)
            items.append(
                {
                    "file": disp,
                    "ai": {
                        "overall": sug.get("overall"),
                        "dims": sug.get("dims") or {},
                        # Rank-derived, not the model's call (see rank_keep_map).
                        "keep": self.keep_rank.get(key),
                        "reason": sug.get("reason") or "",
                        "strongest": sug.get("strongest_aspect") or "",
                        "weakest": sug.get("weakest_aspect") or "",
                        "tags": sug.get("tags") or [],
                        "confidence": sug.get("confidence"),
                        "gate": sug.get("technical_gate"),
                    },
                    "prior": (
                        {"overall": prior.get("overall"), "keep": prior.get("keep")}
                        if prior
                        else None
                    ),
                    "blind": key in self.blind,
                    "reviewed": done is not None,
                    "review": done,
                    "origin": origin,
                }
            )
        return {
            "total": len(items),
            "reviewed": accepted + edited,
            "accepted": accepted,
            "edited": edited,
            "items": items,
        }

    @staticmethod
    def _classify(
        review: dict[str, Any], sug: dict[str, Any], keep_suggested: bool | None
    ) -> str:
        """Accepted means every dimension and keep match what was suggested."""
        ai_dims = sug.get("dims") or {}
        rv_dims = review.get("dims") or {}
        for k in DIM_KEYS:
            a, b = ai_dims.get(k), rv_dims.get(k)
            if a is None or b is None or abs(float(a) - float(b)) > 1e-9:
                return "human_edited"
        if review.get("keep") != keep_suggested:
            return "human_edited"
        return "ai_accepted"

    # --- routes ---
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        route = parsed.path
        if route in ("/", "/index.html"):
            self._send_bytes(PAGE_HTML.encode("utf-8"), "text/html; charset=utf-8", "no-store")
            return
        if route == "/api/meta":
            self._send_json(
                {
                    "dims": DIM_META,
                    "gate": {
                        "dims": list(GATE_DIMS),
                        "threshold": GATE_THRESHOLD,
                        "cap": GATE_CAP,
                    },
                    "reviewer": self.reviewer,
                    "labels_path": str(self.store.path),
                    "log_path": str(self.log_path),
                    "blind_count": len(self.blind),
                    "keep": {
                        "top_pct": self.keep_top_pct,
                        "threshold": self.keep_threshold,
                        "n": sum(1 for v in self.keep_rank.values() if v),
                        "prefill": self.keep_prefill,
                    },
                }
            )
            return
        if route == "/api/queue":
            self._send_json(self._build_queue())
            return
        if route == "/img":
            qs = parse_qs(parsed.query)
            self._serve_image((qs.get("file") or [""])[0])
            return
        self._send_json({"error": "not found"}, status=404)

    def _serve_image(self, fname: str) -> None:
        if not fname:
            self._send_json({"error": "file required"}, status=400)
            return
        p = self.image_index.get(Path(fname).name.lower())
        if p is None or not p.is_file():
            self._send_json({"error": "image not found"}, status=404)
            return
        try:
            data = p.read_bytes()
        except OSError:
            self._send_json({"error": "read failed"}, status=500)
            return
        self._send_bytes(data, _CONTENT_TYPES.get(p.suffix.lower(), "application/octet-stream"))

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/review":
            self._send_json({"error": "not found"}, status=404)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length > 0 else b"{}"
            body = json.loads(raw.decode("utf-8"))
            record = _sanitize_review(body, self.weights)
            gate = record.pop("_gate", None)
            total = self.store.upsert(record)
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json({"error": str(exc)}, status=400)
            return
        except Exception as exc:  # noqa: BLE001
            self._send_json({"error": f"save failed: {exc}"}, status=500)
            return

        key = normalize_name(record["file"])
        sug = self.suggestions.get(key) or {}
        keep_suggested = self.keep_rank.get(key)
        origin = self._classify(record, sug, keep_suggested) if sug else "human_only"
        _append_jsonl(
            self.log_path,
            {
                "schema_version": "relabel_review.v1",
                "file": record["file"],
                "reviewer": self.reviewer,
                "reviewed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "origin": origin,
                "blind": bool(body.get("blind")),
                "ai_revealed": bool(body.get("revealed")),
                "dwell_ms": _clamp(body.get("dwell_ms"), 0, 3_600_000),
                "final": {k: record[k] for k in ("overall", "dims", "keep", "notes")},
                "ai_at_review": {
                    "overall": sug.get("overall"),
                    "dims": sug.get("dims") or {},
                    "keep_suggested": keep_suggested,
                    "keep_source": f"rank_top_{self.keep_top_pct:.2f}",
                    "model": sug.get("model"),
                    "prompt_sha": sug.get("prompt_sha"),
                },
                "technical_gate": gate,
            },
        )
        self._send_json({"ok": True, "saved": record, "origin": origin, "total": total})


def _load_blind(path: Path | None) -> set[str]:
    """Normalized keys of frames to label with the AI hidden.

    ``None``, a missing file, or an explicit disable token (``-`` / ``none`` /
    ``/dev/null``) means no blind frames — so the AI suggestion stays visible.
    """
    if path is None:
        return set()
    # ``Path('/dev/null').exists()`` is True on macOS/Linux; treat it as "off".
    if path.as_posix() in {"-", "none", "/dev/null"} or path.name in {"-", "none"}:
        return set()
    if not path.exists() or not path.is_file():
        return set()
    try:
        raw = path.read_text(encoding="utf-8").strip()
        if not raw:
            return set()
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return set()
    files = data.get("files") if isinstance(data, dict) else data
    return {normalize_name(str(f)) for f in (files or []) if f}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Review Qwen-VL relabel suggestions (stdlib only)")
    parser.add_argument("--suggestions", default="data/eval/relabel/qwen_suggestions.jsonl")
    parser.add_argument("--images", default="data/eval/images")
    parser.add_argument("--out", default="data/eval/labels.jsonl")
    parser.add_argument(
        "--log",
        default="data/eval/relabel/review_log.jsonl",
        help="append-only audit log",
    )
    parser.add_argument(
        "--prior",
        default="",
        help=(
            "existing labels shown as reference ('' to hide). Left empty because "
            "--out is now the registered dataset, so a prior would be self-referential"
        ),
    )
    parser.add_argument(
        "--blind-split",
        default=None,
        help="JSON with a files[] list to hide AI on; omit for none. "
        "Pass '-' / none / /dev/null to force no blind frames "
        "(default: blind_split.json next to --suggestions if it exists)",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="archive --out aside and start with an empty review file so the "
        "default「未复核」filter shows every frame against the new AI suggestions",
    )
    parser.add_argument(
        "--keep-top-pct",
        type=float,
        default=DEFAULT_KEEP_TOP_PCT,
        help="share of the pool pre-marked as keepers by rank; default: %(default)s",
    )
    parser.add_argument(
        "--no-keep-prefill",
        action="store_true",
        help="leave keep blank and force an explicit K/D on every frame, so the keep "
        "column stays independent of the model's ranking",
    )
    parser.add_argument("--reviewer", default=os.environ.get("USER", "reviewer"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8901)
    args = parser.parse_args(argv)

    sug_path = Path(args.suggestions).expanduser().resolve()
    rows = read_jsonl(sug_path)
    if not rows:
        sys.stderr.write(
            f"no suggestions in {sug_path}\n"
            "run: python scripts/eval/relabel_qwen.py score\n"
        )
        return 2
    suggestions = {normalize_name(str(r.get("file"))): r for r in rows if r.get("file")}

    images_dir = Path(args.images).expanduser().resolve()
    if not images_dir.is_dir():
        sys.stderr.write(f"images dir not found: {images_dir}\n")
        return 2
    image_index = _index_images(images_dir)

    prior: dict[str, dict[str, Any]] = {}
    prior_arg = (args.prior or "").strip()
    if prior_arg and prior_arg != "-" and Path(prior_arg).expanduser().exists():
        for lb in load_labels(Path(prior_arg).expanduser()):
            prior[lb.key] = {"overall": lb.overall, "keep": lb.keep}

    out_path = Path(args.out).expanduser().resolve()
    if args.fresh and out_path.exists() and out_path.stat().st_size > 0:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        archive = out_path.with_name(f"{out_path.stem}.before_fresh_{stamp}{out_path.suffix}")
        out_path.replace(archive)
        print(f"fresh        archived previous reviews -> {archive.name}")
        out_path.write_text("", encoding="utf-8")
    log_path = (
        Path(args.log).expanduser().resolve()
        if args.log
        else out_path.with_name("review_log.jsonl")
    )
    if args.blind_split is None:
        candidate = sug_path.with_name("blind_split.json")
        blind_path = candidate if candidate.is_file() else None
    else:
        blind_path = Path(args.blind_split).expanduser()

    Handler.images_dir = images_dir
    Handler.store = LabelStore(out_path)
    Handler.log_path = log_path
    Handler.suggestions = suggestions
    Handler.prior = prior
    Handler.blind = _load_blind(blind_path)
    Handler.image_index = image_index
    Handler.reviewer = str(args.reviewer)
    Handler.weights = normalize_weights(DIM_WEIGHTS)
    keep_rank, keep_threshold = rank_keep_map(rows, args.keep_top_pct)
    Handler.keep_rank = keep_rank
    Handler.keep_threshold = keep_threshold
    Handler.keep_top_pct = float(args.keep_top_pct)
    Handler.keep_prefill = not args.no_keep_prefill

    missing = [k for k in suggestions if Path(suggestions[k]["file"]).name.lower() not in image_index]
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"suggestions   {len(suggestions)} from {sug_path.name}")
    if missing:
        print(f"              {len(missing)} have no image on disk and will fail to display")
    print(f"labels ->     {out_path}")
    print(f"audit  ->     {log_path}")
    print(f"prior ref     {'on (' + str(len(prior)) + ' rows)' if prior else 'off'}")
    n_keep = sum(1 for v in keep_rank.values() if v)
    thr = f"overall >= {keep_threshold:.1f}" if keep_threshold is not None else "n/a"
    print(f"keep preset   {n_keep} of {len(keep_rank)} by rank (top {args.keep_top_pct:.0%}, {thr})")
    print(f"blind frames  {len(Handler.blind)} (AI hidden until you press R)")
    print(f"reviewer      {Handler.reviewer}")
    print(f"\nOpen http://{args.host}:{args.port}   (Ctrl-C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
    finally:
        httpd.server_close()
    return 0


PAGE_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Stage3 复核台</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin:0; font-family: system-ui,-apple-system,"PingFang SC",sans-serif; background:#0b0b0d; color:#e7e7ea; }
  header { display:flex; align-items:center; gap:.9rem; padding:.55rem 1rem; border-bottom:1px solid #1d1d22; position:sticky; top:0; background:#0b0b0d; z-index:5; }
  header h1 { font-size:.9rem; font-weight:600; margin:0; white-space:nowrap; }
  .progress { flex:1; height:6px; background:#1d1d22; border-radius:99px; overflow:hidden; max-width:280px; }
  .progress > div { height:100%; background:linear-gradient(90deg,#34d399,#10b981); width:0%; transition:width .3s; }
  .muted { color:#8a8a93; font-size:.78rem; white-space:nowrap; }
  select { background:#141419; border:1px solid #26262e; color:#e7e7ea; border-radius:7px; padding:.3rem .45rem; font-size:.78rem; }
  main { display:grid; grid-template-columns: minmax(0,1.5fr) minmax(390px,.8fr); gap:1px; height:calc(100vh - 47px); }
  .stage { background:#000; display:flex; align-items:center; justify-content:center; overflow:hidden; position:relative; }
  .stage img { max-width:100%; max-height:100%; object-fit:contain; }
  .stage .fname { position:absolute; left:0; right:0; bottom:0; padding:.4rem .7rem; font-size:.75rem; background:linear-gradient(transparent,#000c); color:#cfcfd6; display:flex; justify-content:space-between; gap:1rem; }
  .badge { font-size:.68rem; padding:.1rem .45rem; border-radius:99px; background:#1d1d22; color:#9a9aa3; }
  .badge.blind { background:#422006; color:#fcd34d; }
  .badge.done { background:#064e3b; color:#a7f3d0; }
  .badge.edit { background:#1e3a8a; color:#bfdbfe; }
  .panel { background:#0e0e12; padding:.85rem 1rem 1.4rem; overflow-y:auto; }
  .hero { display:flex; align-items:center; gap:.8rem; padding:.6rem .75rem; background:#121217; border:1px solid #21212a; border-radius:10px; margin-bottom:.7rem; }
  .hero .big { font-size:2rem; font-weight:700; line-height:1; font-variant-numeric:tabular-nums; }
  .hero .big.moved { color:#fcd34d; }
  .hero .sub { font-size:.7rem; color:#8a8a93; margin-top:.2rem; }
  .keep { display:flex; gap:.4rem; margin-left:auto; }
  .keep button { padding:.45rem .6rem; border-radius:8px; border:1px solid #2a2a33; background:#141419; color:#cfcfd6; cursor:pointer; font-size:.8rem; white-space:nowrap; }
  .keep button.on-keep { background:#064e3b; border-color:#10b981; color:#a7f3d0; }
  .keep button.on-drop { background:#4c0519; border-color:#f43f5e; color:#fecdd3; }
  .aibox { background:#101015; border:1px solid #1e1e26; border-left:2px solid #6366f1; border-radius:8px; padding:.5rem .65rem; margin-bottom:.7rem; font-size:.82rem; line-height:1.5; }
  .aibox .lbl { font-size:.65rem; text-transform:uppercase; letter-spacing:.09em; color:#7a7a83; margin-bottom:.25rem; }
  .aibox .sw { color:#a3a3ad; font-size:.76rem; margin-top:.3rem; }
  .aibox.hidden { border-left-color:#f59e0b; color:#8a8a93; font-style:italic; }
  .gate { background:#3f1d1d; border:1px solid #7f1d1d; color:#fecaca; border-radius:7px; padding:.35rem .6rem; font-size:.74rem; margin-bottom:.6rem; }
  table.dims { width:100%; border-collapse:collapse; margin-bottom:.7rem; }
  table.dims th { text-align:left; font-size:.63rem; text-transform:uppercase; letter-spacing:.08em; color:#7a7a83; font-weight:500; padding:0 .3rem .3rem; }
  table.dims td { padding:.16rem .3rem; border-top:1px solid #17171d; vertical-align:middle; }
  table.dims tr.changed td { background:#141b2e; }
  .dname { font-size:.83rem; }
  .dkey { font-size:.62rem; color:#6b6b73; }
  .dw { font-size:.62rem; color:#6b6b73; font-variant-numeric:tabular-nums; }
  .dai { font-size:.82rem; color:#8a8a93; font-variant-numeric:tabular-nums; text-align:center; width:2.6rem; }
  table.dims input { width:3.5rem; background:#141419; border:1px solid #26262e; color:#e7e7ea; border-radius:6px; padding:.25rem .3rem; font-size:.9rem; text-align:center; font-variant-numeric:tabular-nums; }
  table.dims input:focus { outline:none; border-color:#10b981; background:#0f1a16; }
  .ddelta { font-size:.7rem; width:2.4rem; text-align:right; font-variant-numeric:tabular-nums; }
  .ddelta.up { color:#34d399; } .ddelta.down { color:#f87171; }
  label.fld { display:block; font-size:.65rem; text-transform:uppercase; letter-spacing:.08em; color:#7a7a83; margin:.5rem 0 .25rem; }
  textarea { width:100%; background:#141419; border:1px solid #26262e; color:#e7e7ea; border-radius:8px; padding:.4rem .55rem; resize:vertical; min-height:38px; font-family:inherit; font-size:.85rem; }
  .nav { display:flex; gap:.45rem; margin-top:.7rem; }
  .nav button { padding:.55rem .5rem; border-radius:8px; border:1px solid #2a2a33; background:#141419; color:#e7e7ea; cursor:pointer; font-size:.85rem; }
  .nav button.primary { flex:1; background:#10b981; border-color:#10b981; color:#04231a; font-weight:600; }
  .nav button:hover { filter:brightness(1.15); }
  .prior { font-size:.74rem; color:#7a7a83; margin-top:.55rem; }
  .prior b { color:#a3a3ad; font-weight:600; }
  .hint { font-size:.68rem; color:#61616a; margin-top:.7rem; line-height:1.65; }
  kbd { background:#1d1d22; border:1px solid #2a2a33; border-radius:4px; padding:0 .25rem; font-size:.66rem; font-family:inherit; }
  .empty { padding:2rem; color:#8a8a93; }
</style>
</head>
<body>
<header>
  <h1>Stage3 复核台</h1>
  <div class="progress"><div id="bar"></div></div>
  <span class="muted" id="counts">0 / 0</span>
  <span class="muted" id="split"></span>
  <select id="filter" style="margin-left:auto">
      <option value="unreviewed">未复核</option>
      <option value="nokeep">keep 未定</option>
    <option value="all">全部</option>
    <option value="blind">盲标（AI 隐藏）</option>
    <option value="edited">我改过的</option>
    <option value="accepted">直接接受的</option>
    <option value="disagree">AI 与旧标签差 ≥15</option>
  </select>
</header>
<main>
  <div class="stage">
    <img id="photo" alt="" />
    <div class="fname">
      <span id="fname"></span>
      <span id="flags"></span>
    </div>
  </div>
  <div class="panel" id="panel">
    <div class="hero">
      <div>
        <div class="big" id="overall">--</div>
        <div class="sub" id="overallSub">由 8 个维度加权得出</div>
      </div>
      <div class="keep">
        <button type="button" id="btnKeep">★ 保留</button>
        <button type="button" id="btnDrop">✕ 弃</button>
      </div>
    </div>
    <div class="gate" id="gate" style="display:none"></div>
    <div class="prior" id="keepNote" style="margin:0 0 .6rem"></div>
    <div class="aibox" id="aibox"></div>
    <table class="dims">
      <thead><tr><th>维度</th><th style="text-align:center">AI</th><th style="text-align:center">你</th><th></th></tr></thead>
      <tbody id="dims"></tbody>
    </table>
    <label class="fld">备注</label>
    <textarea id="notes" placeholder="只在需要解释判断时写"></textarea>
    <div class="nav">
      <button type="button" id="prev">←</button>
      <button type="button" class="primary" id="saveNext">保存并下一张 (Enter)</button>
      <button type="button" id="next">跳过 →</button>
    </div>
    <div class="prior" id="prior"></div>
    <div class="hint">
      <kbd>Enter</kbd> 保存并下一张 · <kbd>←</kbd><kbd>→</kbd> 切换 · <kbd>K</kbd> 保留 · <kbd>D</kbd> 弃 ·
      <kbd>1</kbd>–<kbd>8</kbd> 跳到第 N 个维度 · <kbd>R</kbd> 揭示盲标的 AI 分 · <kbd>0</kbd> 复原为 AI 建议<br>
      整体分不能手输——改维度，整体分自动重算。维度与 AI 完全一致才记为「直接接受」。
    </div>
  </div>
</main>
<script>
let META = null, DIMS = [], GATE = null;
let ALL = [], VIEW = [], idx = 0;
let keepState = null, revealed = false, enteredAt = Date.now();

const $ = (id) => document.getElementById(id);
const cur = () => VIEW[idx];

async function boot() {
  META = await (await fetch('/api/meta')).json();
  DIMS = META.dims; GATE = META.gate;
  const k = META.keep || {};
  $('keepNote').innerHTML = !k.prefill
    ? 'keep <b>不预填</b>：每张都要按 <kbd>K</kbd> 或 <kbd>D</kbd> 明确表态，没表态存不了。这一列因此完全独立于模型排序。'
    : (k.threshold != null
      ? `keep 是<b>按 overall 排序</b>预设的（前 ${Math.round(k.top_pct*100)}%，阈值 ${k.threshold.toFixed(1)}），
         不是模型的判断。它不含 overall 之外的信息，需要你主动推翻。`
      : 'keep 需要你自己判断。');
  buildDimRows();
  bind();
  await refresh(true);
}

function buildDimRows() {
  const tb = $('dims');
  tb.innerHTML = '';
  DIMS.forEach((d, i) => {
    const tr = document.createElement('tr');
    tr.id = 'row_' + d.key;
    tr.innerHTML = `
      <td><div class="dname">${i+1}. ${d.label}</div><div class="dkey">${d.key} <span class="dw">w=${d.weight.toFixed(2)}</span></div></td>
      <td class="dai" id="ai_${d.key}">–</td>
      <td><input type="number" min="0" max="10" step="0.1" id="dim_${d.key}" /></td>
      <td class="ddelta" id="delta_${d.key}"></td>`;
    tb.appendChild(tr);
  });
  for (const d of DIMS) {
    $('dim_' + d.key).addEventListener('input', onDimInput);
  }
}

function computeOverall(dims) {
  let blend = 0;
  for (const d of DIMS) {
    const v = dims[d.key];
    if (v === null || v === undefined || Number.isNaN(v)) return null;
    blend += v * d.weight;
  }
  blend *= 10;
  let gated = null;
  for (const k of GATE.dims) {
    if (dims[k] <= GATE.threshold && blend > GATE.cap) { gated = k; blend = GATE.cap; break; }
  }
  return { value: Math.round(blend * 10) / 10, gated };
}

function readDims() {
  const out = {};
  for (const d of DIMS) {
    const raw = $('dim_' + d.key).value;
    out[d.key] = raw === '' ? null : Number(raw);
  }
  return out;
}

function onDimInput() {
  const dims = readDims();
  const it = cur();
  const ai = (it && it.ai && it.ai.dims) || {};
  for (const d of DIMS) {
    const mine = dims[d.key], a = ai[d.key];
    const row = $('row_' + d.key), del = $('delta_' + d.key);
    const diff = (mine !== null && a !== null && a !== undefined) ? Math.round((mine - a) * 10) / 10 : null;
    const changed = diff !== null && Math.abs(diff) > 1e-9;
    row.classList.toggle('changed', changed);
    del.textContent = changed ? (diff > 0 ? '+' + diff : String(diff)) : '';
    del.className = 'ddelta ' + (changed ? (diff > 0 ? 'up' : 'down') : '');
  }
  const res = computeOverall(dims);
  const aiOverall = it && it.ai ? it.ai.overall : null;
  $('overall').textContent = res ? res.value.toFixed(1) : '--';
  $('overall').classList.toggle('moved', !!(res && aiOverall != null && Math.abs(res.value - aiOverall) > 0.05));
  if (res && res.gated) {
    $('gate').style.display = '';
    $('gate').textContent = `技术门槛生效：${res.gated} ≤ ${GATE.threshold}，整体分封顶 ${GATE.cap}`;
  } else {
    $('gate').style.display = 'none';
  }
  const sub = (res && aiOverall != null) ? `AI 建议 ${Number(aiOverall).toFixed(1)}` : '由 8 个维度加权得出';
  $('overallSub').textContent = sub;
}

async function refresh(jump) {
  const data = await (await fetch('/api/queue')).json();
  ALL = data.items;
  $('bar').style.width = (data.total ? 100 * data.reviewed / data.total : 0) + '%';
  $('counts').textContent = data.reviewed + ' / ' + data.total;
  $('split').textContent = '接受 ' + data.accepted + ' · 改过 ' + data.edited
    + (data.reviewed ? ' · 改动率 ' + Math.round(100 * data.edited / data.reviewed) + '%' : '');
  applyFilter(jump);
}

function applyFilter(jump) {
  let f = $('filter').value;
  // Previous review pass left every frame marked reviewed. The default
  //「未复核」filter then shows an empty stage (no photo, no AI) — fall back.
  if (f === 'unreviewed' && ALL.length && !ALL.some(it => !it.reviewed)) {
    f = 'all';
    $('filter').value = 'all';
  }
  VIEW = ALL.filter(it => {
    if (f === 'all') return true;
    if (f === 'unreviewed') return !it.reviewed;
    if (f === 'nokeep') return it.reviewed && (!it.review || it.review.keep === null || it.review.keep === undefined);
    if (f === 'blind') return it.blind;
    if (f === 'edited') return it.origin === 'human_edited';
    if (f === 'accepted') return it.origin === 'ai_accepted';
    if (f === 'disagree') return it.prior && it.prior.overall != null && it.ai.overall != null
      && Math.abs(it.prior.overall - it.ai.overall) >= 15;
    return true;
  });
  if (jump || idx >= VIEW.length) idx = 0;
  load();
}

function load() {
  if (!VIEW.length) {
    $('photo').removeAttribute('src');
    $('fname').textContent = '';
    $('flags').textContent = '';
    const f = $('filter').value;
    const tip = (f === 'unreviewed' && ALL.length)
      ? `「未复核」下没有条目——上一轮的 ${ALL.length} 张都已写入 labels 了。请把筛选改成「全部」，或用 <code>--fresh</code> 重启以清空旧复核。`
      : '这个筛选下没有条目。';
    $('aibox').innerHTML = '<span class="muted">' + tip + '</span>';
    return;
  }
  const it = cur();
  revealed = false;
  enteredAt = Date.now();
  $('photo').src = '/img?file=' + encodeURIComponent(it.file);
  $('fname').textContent = it.file + '  (' + (idx + 1) + '/' + VIEW.length + ')';
  const flags = [];
  if (it.blind) flags.push('<span class="badge blind">盲标</span>');
  if (it.origin === 'ai_accepted') flags.push('<span class="badge done">已接受</span>');
  if (it.origin === 'human_edited') flags.push('<span class="badge edit">已修改</span>');
  if (!it.reviewed) flags.push('<span class="badge">未复核</span>');
  if (it.ai.confidence != null) flags.push('<span class="badge">AI 置信 ' + Number(it.ai.confidence).toFixed(2) + '</span>');
  $('flags').innerHTML = flags.join(' ');

  const showAI = !it.blind || it.reviewed;
  renderAI(it, showAI);

  const rv = it.review;
  for (const d of DIMS) {
    const aiV = it.ai.dims ? it.ai.dims[d.key] : null;
    $('ai_' + d.key).textContent = showAI && aiV != null ? Number(aiV).toFixed(1) : '–';
    let v = null;
    if (rv && rv.dims && rv.dims[d.key] != null) v = rv.dims[d.key];
    else if (!it.blind && aiV != null) v = aiV;
    $('dim_' + d.key).value = (v === null ? '' : v);
  }
    const prefillKeep = META.keep && META.keep.prefill;
    setKeep(rv ? rv.keep : ((it.blind || !prefillKeep) ? null : it.ai.keep));
  $('notes').value = (rv && rv.notes) || '';
  $('prior').innerHTML = it.prior
    ? `旧人工标签：<b>${it.prior.overall != null ? it.prior.overall : '—'}</b> · keep=<b>${it.prior.keep === null ? '—' : it.prior.keep}</b>`
    : '';
  onDimInput();
}

function renderAI(it, show) {
  const box = $('aibox');
  if (!show) {
    box.className = 'aibox hidden';
    box.innerHTML = '盲标帧：AI 分数与理由已隐藏。先自己判断，需要时按 <kbd>R</kbd> 揭示。';
    return;
  }
  box.className = 'aibox';
  const tags = (it.ai.tags || []).slice(0, 8).join(' · ');
  box.innerHTML = `<div class="lbl">AI 判断</div>${it.ai.reason || '（无理由）'}
    <div class="sw">强：${it.ai.strongest || '—'} ｜ 弱：${it.ai.weakest || '—'}</div>
    ${tags ? '<div class="sw">' + tags + '</div>' : ''}`;
}

function reveal() {
  const it = cur();
  if (!it || !it.blind) return;
  revealed = true;
  renderAI(it, true);
  for (const d of DIMS) {
    const v = it.ai.dims ? it.ai.dims[d.key] : null;
    $('ai_' + d.key).textContent = v != null ? Number(v).toFixed(1) : '–';
  }
  onDimInput();
}

function resetToAI() {
  const it = cur();
  if (!it || !it.ai.dims) return;
  for (const d of DIMS) {
    const v = it.ai.dims[d.key];
    $('dim_' + d.key).value = (v == null ? '' : v);
  }
  setKeep(it.ai.keep);
  onDimInput();
}

function setKeep(v) {
  keepState = (typeof v === 'boolean') ? v : null;
  $('btnKeep').classList.toggle('on-keep', keepState === true);
  $('btnDrop').classList.toggle('on-drop', keepState === false);
}

async function save() {
  const it = cur();
  if (!it) return false;
  const dims = readDims();
      const missing = DIMS.filter(d => dims[d.key] === null);
      if (missing.length) {
        alert('还有维度没填：' + missing.map(d => d.label).join('、'));
        $('dim_' + missing[0].key).focus();
        return false;
      }
      if (keepState === null) {
        alert('还没决定 keep：按 K 保留，按 D 弃。');
        return false;
      }
  const res = await fetch('/api/review', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      file: it.file, dims, keep: keepState, notes: $('notes').value || '',
      blind: !!it.blind, revealed, dwell_ms: Date.now() - enteredAt
    })
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    alert('保存失败：' + (err.error || res.status));
    return false;
  }
  const out = await res.json();
  it.review = out.saved; it.reviewed = true; it.origin = out.origin;
  const reviewed = ALL.filter(x => x.reviewed).length;
  const edited = ALL.filter(x => x.origin === 'human_edited').length;
  $('bar').style.width = (100 * reviewed / ALL.length) + '%';
  $('counts').textContent = reviewed + ' / ' + ALL.length;
  $('split').textContent = '接受 ' + (reviewed - edited) + ' · 改过 ' + edited
    + (reviewed ? ' · 改动率 ' + Math.round(100 * edited / reviewed) + '%' : '');
  return true;
}

async function saveNext() { if (await save()) go(+1); }
function go(dir) {
  if (!VIEW.length) return;
  idx = (idx + dir + VIEW.length) % VIEW.length;
  load();
}

function bind() {
  $('saveNext').onclick = saveNext;
  $('prev').onclick = () => go(-1);
  $('next').onclick = () => go(+1);
    // No toggle-to-null: keep is pre-filled, so "press K to confirm a keeper"
    // must not silently clear the decision.
    $('btnKeep').onclick = () => setKeep(true);
    $('btnDrop').onclick = () => setKeep(false);
  $('filter').onchange = () => applyFilter(true);
  document.addEventListener('keydown', (e) => {
    const tag = (e.target.tagName || '').toLowerCase();
    const typing = tag === 'textarea' || tag === 'input';
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); saveNext(); return; }
    if (e.key === 'Escape') { e.target.blur && e.target.blur(); return; }
    if (typing && tag !== 'input') return;
    if (typing && !['ArrowLeft','ArrowRight'].includes(e.key)) return;
    if (e.key === 'ArrowRight') { e.preventDefault(); go(+1); }
    else if (e.key === 'ArrowLeft') { e.preventDefault(); go(-1); }
    else if (e.key >= '1' && e.key <= '8') {
      const d = DIMS[Number(e.key) - 1];
      if (d) { e.preventDefault(); const el = $('dim_' + d.key); el.focus(); el.select(); }
    }
    else if (e.key === '0') { e.preventDefault(); resetToAI(); }
      else if (e.key.toLowerCase() === 'k') { setKeep(true); }
      else if (e.key.toLowerCase() === 'd') { setKeep(false); }
    else if (e.key.toLowerCase() === 'r') { reveal(); }
  });
}

boot();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
