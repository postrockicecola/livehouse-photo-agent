#!/usr/bin/env python3
"""Local orientation-review UI for a frozen-candidate manifest.

Corrections are stored as metadata; source images are never modified.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse


VALID_ROTATIONS = {0, 90, 180, 270}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class OrientationStore:
    """Thread-safe, atomic persistence for orientation review decisions."""

    def __init__(self, path: Path, valid_files: set[str]) -> None:
        self.path = path
        self.valid_files = valid_files
        self._lock = threading.Lock()
        self._items: dict[str, dict[str, Any]] = {}
        if path.is_file():
            raw = json.loads(path.read_text(encoding="utf-8"))
            for file_id, value in (raw.get("items") or {}).items():
                if file_id in valid_files and isinstance(value, dict):
                    degrees = int(value.get("rotation_degrees") or 0)
                    if degrees in VALID_ROTATIONS:
                        self._items[file_id] = {
                            "rotation_degrees": degrees,
                            "reviewed": bool(value.get("reviewed")),
                            "updated_at": value.get("updated_at"),
                        }

    def snapshot(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {key: dict(value) for key, value in self._items.items()}

    def update(
        self, file_id: str, rotation_degrees: int, reviewed: bool
    ) -> dict[str, Any]:
        if file_id not in self.valid_files:
            raise ValueError("unknown file")
        if rotation_degrees not in VALID_ROTATIONS:
            raise ValueError("rotation_degrees must be 0, 90, 180, or 270")
        value = {
            "rotation_degrees": rotation_degrees,
            "reviewed": reviewed,
            "updated_at": _now(),
        }
        with self._lock:
            self._items[file_id] = value
            payload = {
                "schema_version": "selection_orientation_review.v1",
                "updated_at": value["updated_at"],
                "items": self._items,
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(self.path)
        return dict(value)


def load_manifest(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw.get("items") or []:
        file_id = Path(str(item.get("file") or "")).name
        source = Path(str(item.get("source_path") or ""))
        if not file_id or file_id in seen:
            raise ValueError(f"{path}: duplicate or empty file id {file_id!r}")
        if not source.is_file():
            raise FileNotFoundError(source)
        seen.add(file_id)
        rows.append(
            {
                "file": file_id,
                "source_path": source,
                "sample_type": str(item.get("sample_type") or "unknown"),
                "session": str(item.get("session") or ""),
            }
        )
    if not rows:
        raise ValueError(f"{path}: manifest contains no items")
    return rows


class OrientationHandler(BaseHTTPRequestHandler):
    rows: list[dict[str, Any]]
    image_index: dict[str, Path]
    store: OrientationStore

    server_version = "OrientationReview/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        print("[orientation] " + (fmt % args))

    def _send_json(self, value: Any, status: int = 200) -> None:
        data = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_bytes(self, data: bytes, content_type: str, cache: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        route = urlparse(self.path).path
        if route in {"/", "/index.html"}:
            self._send_bytes(
                PAGE_HTML.encode("utf-8"),
                "text/html; charset=utf-8",
                "no-store",
            )
            return
        if route == "/api/state":
            reviews = self.store.snapshot()
            items = [
                {
                    "file": row["file"],
                    "sample_type": row["sample_type"],
                    "session": row["session"],
                    **reviews.get(
                        row["file"],
                        {"rotation_degrees": 0, "reviewed": False},
                    ),
                }
                for row in self.rows
            ]
            self._send_json({"total": len(items), "items": items})
            return
        if route.startswith("/image/"):
            file_id = unquote(route.removeprefix("/image/"))
            source = self.image_index.get(file_id)
            if source is None:
                self.send_error(404, "unknown image")
                return
            content_type = mimetypes.guess_type(source.name)[0] or "image/jpeg"
            self._send_bytes(source.read_bytes(), content_type, "private, max-age=300")
            return
        self.send_error(404)

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/orientation":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > 16_384:
                raise ValueError("invalid request size")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("request body must be an object")
            file_id = str(body.get("file") or "")
            degrees = int(body.get("rotation_degrees"))
            reviewed = body.get("reviewed")
            if not isinstance(reviewed, bool):
                raise ValueError("reviewed must be boolean")
            value = self.store.update(file_id, degrees, reviewed)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self._send_json({"error": str(exc)}, status=400)
            return
        self._send_json({"file": file_id, **value})


PAGE_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>图像方向复核</title>
  <style>
    :root { color-scheme: dark; font-family: system-ui, sans-serif; }
    * { box-sizing: border-box; }
    body { margin: 0; background: #0f1115; color: #eef1f6; }
    header { position: sticky; top: 0; z-index: 3; padding: 14px 18px;
      background: rgba(15,17,21,.96); border-bottom: 1px solid #343a46; }
    h1 { margin: 0; font-size: 21px; }
    .summary { margin-top: 6px; color: #aeb7c7; font-size: 13px; }
    .toolbar { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 11px; }
    button, input { color: inherit; background: #20242d; border: 1px solid #414957;
      border-radius: 7px; padding: 7px 10px; }
    button { cursor: pointer; }
    button.active { background: #e8edf5; color: #11141a; }
    input { min-width: 220px; }
    main { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
      gap: 14px; padding: 16px; }
    .card { min-width: 0; border: 1px solid #343a46; border-radius: 10px;
      overflow: hidden; background: #181b21; }
    .card.selected { outline: 2px solid #64a4ff; }
    .card.reviewed { border-color: #347653; }
    .viewport { width: 100%; aspect-ratio: 1; display: grid; place-items: center;
      overflow: hidden; background: #08090c; cursor: pointer; }
    .viewport img { display: block; max-width: 100%; max-height: 100%;
      object-fit: contain; transition: transform .15s ease; }
    .body { padding: 10px; }
    .title { font-size: 13px; overflow: hidden; text-overflow: ellipsis;
      white-space: nowrap; }
    .meta { color: #909aab; font-size: 12px; margin-top: 5px; }
    .actions { display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px;
      margin-top: 9px; }
    .actions button { padding: 7px 4px; }
    .ok { color: #7fe1a7; }
    .changed { color: #ffbf69; }
    .hidden { display: none; }
  </style>
</head>
<body>
  <header>
    <h1>图像方向复核</h1>
    <div class="summary" id="summary">正在载入…</div>
    <div class="toolbar">
      <input id="search" placeholder="搜索文件名">
      <button class="active" data-filter="all">全部</button>
      <button data-filter="unreviewed">未复核</button>
      <button data-filter="changed">已旋转</button>
      <button data-filter="reviewed">已复核</button>
      <button id="next">下一个未复核 (J)</button>
    </div>
  </header>
  <main id="grid"></main>
  <script>
    const state = { items: [], filter: 'all', selected: null };
    const grid = document.querySelector('#grid');
    const summary = document.querySelector('#summary');
    const search = document.querySelector('#search');
    const esc = value => value.replace(/[&<>"']/g, c =>
      ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

    function status() {
      const reviewed = state.items.filter(x => x.reviewed).length;
      const changed = state.items.filter(x => x.rotation_degrees !== 0).length;
      summary.textContent = `已复核 ${reviewed}/${state.items.length} · 已旋转 ${changed} · ` +
        `方向修正自动保存，不修改原始照片`;
    }

    function visible(item) {
      const q = search.value.trim().toLowerCase();
      const match = !q || item.file.toLowerCase().includes(q);
      const f = state.filter;
      return match && (f === 'all' || (f === 'unreviewed' && !item.reviewed) ||
        (f === 'changed' && item.rotation_degrees !== 0) ||
        (f === 'reviewed' && item.reviewed));
    }

    function render() {
      grid.innerHTML = state.items.map((item, index) => {
        const classes = ['card', item.reviewed ? 'reviewed' : '',
          state.selected === index ? 'selected' : '', visible(item) ? '' : 'hidden'].join(' ');
        const statusClass = item.rotation_degrees ? 'changed' : item.reviewed ? 'ok' : '';
        const text = item.rotation_degrees ? `顺时针 ${item.rotation_degrees}°` :
          item.reviewed ? '方向正确' : '尚未复核';
        return `<article class="${classes}" data-index="${index}">
          <div class="viewport"><img loading="lazy" src="/image/${encodeURIComponent(item.file)}"
            style="transform:rotate(${item.rotation_degrees}deg)" alt="${esc(item.file)}"></div>
          <div class="body">
            <div class="title">#${String(index + 1).padStart(3,'0')} ${esc(item.file)}</div>
            <div class="meta">${esc(item.sample_type)} · ${esc(item.session)}
              · <span class="${statusClass}">${text}</span></div>
            <div class="actions">
              <button data-action="left">↶ 左转</button>
              <button data-action="ok">✓ 正确</button>
              <button data-action="right">↷ 右转</button>
            </div>
          </div>
        </article>`;
      }).join('');
      status();
    }

    async function save(index, degrees, reviewed=true) {
      const item = state.items[index];
      const response = await fetch('/api/orientation', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({file:item.file, rotation_degrees:degrees, reviewed})
      });
      if (!response.ok) throw new Error((await response.json()).error || '保存失败');
      Object.assign(item, await response.json());
      state.selected = index;
      render();
    }

    function rotate(index, delta) {
      return save(index, (state.items[index].rotation_degrees + delta + 360) % 360);
    }

    grid.addEventListener('click', event => {
      const card = event.target.closest('.card');
      if (!card) return;
      const index = Number(card.dataset.index);
      state.selected = index;
      const action = event.target.closest('button')?.dataset.action;
      if (action === 'left') rotate(index, -90);
      else if (action === 'right') rotate(index, 90);
      else if (action === 'ok') save(index, state.items[index].rotation_degrees);
      else render();
    });

    function nextUnreviewed() {
      const start = state.selected === null ? -1 : state.selected;
      for (let offset = 1; offset <= state.items.length; offset++) {
        const index = (start + offset) % state.items.length;
        if (!state.items[index].reviewed) {
          state.selected = index;
          render();
          document.querySelector(`[data-index="${index}"]`)?.scrollIntoView(
            {behavior:'smooth', block:'center'});
          return;
        }
      }
    }

    document.querySelector('#next').addEventListener('click', nextUnreviewed);
    search.addEventListener('input', render);
    document.querySelectorAll('[data-filter]').forEach(button => {
      button.addEventListener('click', () => {
        document.querySelectorAll('[data-filter]').forEach(x => x.classList.remove('active'));
        button.classList.add('active');
        state.filter = button.dataset.filter;
        render();
      });
    });
    document.addEventListener('keydown', event => {
      if (event.target.tagName === 'INPUT') return;
      if (event.key.toLowerCase() === 'j') nextUnreviewed();
      if (state.selected === null) return;
      if (event.key === 'ArrowLeft') rotate(state.selected, -90);
      if (event.key === 'ArrowRight') rotate(state.selected, 90);
      if (event.key === 'Enter') save(
        state.selected, state.items[state.selected].rotation_degrees);
    });

    fetch('/api/state').then(r => r.json()).then(data => {
      state.items = data.items;
      render();
      nextUnreviewed();
    }).catch(error => { summary.textContent = `载入失败：${error}`; });
  </script>
</body>
</html>
"""


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=root / "data/eval/selection_v1/manifest.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=root / "data/eval/selection_v1/orientation_review.json",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8903)
    args = parser.parse_args()

    rows = load_manifest(args.manifest)
    OrientationHandler.rows = rows
    OrientationHandler.image_index = {
        str(row["file"]): Path(row["source_path"]) for row in rows
    }
    OrientationHandler.store = OrientationStore(
        args.output, set(OrientationHandler.image_index)
    )
    server = ThreadingHTTPServer((args.host, args.port), OrientationHandler)
    print(f"Orientation review: http://{args.host}:{args.port}")
    print(f"Saving decisions to: {args.output}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
