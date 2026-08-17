#!/usr/bin/env python3
"""Blind keep/trash review for human_keep_v1. No scores, no sample_type."""
from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.eval.labels import normalize_name


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp.replace(path)


class KeepStore:
    def __init__(self, path: Path, files: list[str]) -> None:
        self.path = path
        self.files = files
        self._lock = threading.Lock()
        existing = {normalize_name(row.get("file")): row for row in _read_jsonl(path)}
        self._items: dict[str, dict[str, Any]] = {}
        for file_id in files:
            prev = existing.get(normalize_name(file_id), {})
            self._items[file_id] = {
                "file": file_id,
                "keep": prev.get("keep") if isinstance(prev.get("keep"), bool) else None,
                "notes": str(prev.get("notes") or ""),
                "label_schema": "human_keep_v1",
                "updated_at": prev.get("updated_at"),
            }

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(self._items[name]) for name in self.files]

    def update(self, file_id: str, keep: bool | None, notes: str = "") -> dict[str, Any]:
        if file_id not in self._items:
            raise ValueError("unknown file")
        value = {
            "file": file_id,
            "keep": keep,
            "notes": notes,
            "label_schema": "human_keep_v1",
            "updated_at": _now(),
        }
        with self._lock:
            self._items[file_id] = value
            _write_jsonl(self.path, [self._items[name] for name in self.files])
        return dict(value)


PAGE_HTML = """<!doctype html>
<html lang="zh-CN"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Human keep 盲标</title>
<style>
:root { color-scheme: dark; font-family: system-ui, sans-serif; }
body { margin: 0; background: #111; color: #eee; }
header { padding: 14px 18px; border-bottom: 1px solid #333; }
h1 { margin: 0; font-size: 20px; }
.meta { color: #9aa; font-size: 13px; margin-top: 6px; }
main { display: grid; place-items: center; padding: 20px; }
img { max-width: min(92vw, 1100px); max-height: 72vh; object-fit: contain; background: #000; }
.actions { display: flex; gap: 10px; justify-content: center; margin-top: 16px; }
button { font-size: 16px; padding: 10px 18px; border-radius: 8px; border: 1px solid #555; background: #222; color: inherit; cursor: pointer; }
button.keep { border-color: #3a7; }
button.drop { border-color: #a44; }
</style></head>
<body>
<header>
  <h1>只看图，标 keep / trash。没有分数，没有类别。</h1>
  <div class="meta" id="status">loading</div>
</header>
<main>
  <img id="photo" alt="">
  <div class="actions">
    <button onclick="move(-1)">上一张</button>
    <button class="keep" onclick="save(true)">Keep (K)</button>
    <button class="drop" onclick="save(false)">Trash (T)</button>
    <button onclick="move(1)">下一张</button>
  </div>
</main>
<script>
let items = [];
let idx = 0;
async function load() {
  const res = await fetch("/api/state");
  const data = await res.json();
  items = data.items;
  idx = items.findIndex(x => x.keep === null);
  if (idx < 0) idx = 0;
  render();
}
function render() {
  const item = items[idx];
  if (!item) return;
  const done = items.filter(x => x.keep !== null).length;
  document.getElementById("status").textContent =
    `${idx + 1}/${items.length}  已标 ${done}  ${item.file}`;
  document.getElementById("photo").src = "/image/" + encodeURIComponent(item.file);
}
async function save(keep) {
  const item = items[idx];
  const res = await fetch("/api/keep", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({file: item.file, keep}),
  });
  items[idx] = await res.json();
  move(1);
}
function move(delta) {
  idx = (idx + delta + items.length) % items.length;
  render();
}
document.addEventListener("keydown", (ev) => {
  if (ev.key === "k" || ev.key === "K") save(true);
  if (ev.key === "t" || ev.key === "T") save(false);
  if (ev.key === "ArrowRight") move(1);
  if (ev.key === "ArrowLeft") move(-1);
});
load();
</script>
</body></html>
"""


class KeepHandler(BaseHTTPRequestHandler):
    rows: list[dict[str, Any]]
    image_index: dict[str, Path]
    store: KeepStore

    def log_message(self, fmt: str, *args: Any) -> None:
        print("[human-keep] " + (fmt % args))

    def _send(self, data: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        route = urlparse(self.path).path
        if route in {"/", "/index.html"}:
            self._send(PAGE_HTML.encode("utf-8"), "text/html; charset=utf-8")
            return
        if route == "/api/state":
            payload = json.dumps({"items": self.store.snapshot()}, ensure_ascii=False).encode("utf-8")
            self._send(payload, "application/json; charset=utf-8")
            return
        if route.startswith("/image/"):
            file_id = unquote(route.removeprefix("/image/"))
            source = self.image_index.get(file_id)
            if source is None or not source.is_file():
                self.send_error(404, "unknown image")
                return
            content_type = mimetypes.guess_type(source.name)[0] or "image/jpeg"
            self._send(source.read_bytes(), content_type)
            return
        self.send_error(404)

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/keep":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length))
            file_id = str(body.get("file") or "")
            keep = body.get("keep")
            if keep is not None and not isinstance(keep, bool):
                raise ValueError("keep must be bool or null")
            value = self.store.update(file_id, keep, str(body.get("notes") or ""))
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self._send(json.dumps({"error": str(exc)}).encode("utf-8"), "application/json", 400)
            return
        self._send(json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")


def _resolve_image(row: dict[str, Any], normalized_dir: Path) -> Path | None:
    file_id = str(row["file"])
    local = normalized_dir / file_id
    if local.is_file():
        return local
    source = Path(str(row.get("source_path") or ""))
    return source if source.is_file() else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", type=Path, default=ROOT / "data/eval/human_keep_v1/irr_queue.jsonl")
    parser.add_argument("--out", type=Path, default=ROOT / "data/eval/human_keep_v1/labels_r2.jsonl")
    parser.add_argument("--images", type=Path, default=ROOT / "data/eval/selection_v1/normalized_images")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8903)
    args = parser.parse_args(argv)
    rows = _read_jsonl(args.queue)
    if not rows:
        print(f"error: empty queue {args.queue} — run build_human_keep_v1.py first", file=sys.stderr)
        return 2
    image_index: dict[str, Path] = {}
    missing = 0
    for row in rows:
        path = _resolve_image(row, args.images)
        if path is None:
            missing += 1
            continue
        image_index[str(row["file"])] = path
    if missing:
        print(f"warn: {missing} queue images not found locally", file=sys.stderr)
    if not image_index:
        print("error: no reviewable images", file=sys.stderr)
        return 2
    KeepHandler.rows = rows
    KeepHandler.image_index = image_index
    KeepHandler.store = KeepStore(args.out, [str(row["file"]) for row in rows if str(row["file"]) in image_index])
    server = ThreadingHTTPServer((args.host, args.port), KeepHandler)
    print(f"Human keep review: http://{args.host}:{args.port}")
    print(f"Writing {args.out}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
