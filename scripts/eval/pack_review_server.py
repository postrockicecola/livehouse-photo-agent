#!/usr/bin/env python3
"""Local contact-sheet UI for ordered Top-5 pack preference review."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

_CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
_write_lock = threading.Lock()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _prediction_rows(path: Path) -> list[dict[str, Any]]:
    raw = _read_json(path)
    if isinstance(raw, dict):
        raw = raw.get("results") or []
    return [row for row in raw if isinstance(row, dict)]


def _file_id(row: dict[str, Any]) -> str:
    return str(row.get("file") or row.get("file_name") or row.get("image") or "")


class ReviewStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def read_all(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        return [
            json.loads(line)
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def as_map(self) -> dict[str, dict[str, Any]]:
        return {str(row["pack_id"]): row for row in self.read_all()}

    def upsert(self, record: dict[str, Any]) -> int:
        with _write_lock:
            rows = self.read_all()
            for index, row in enumerate(rows):
                if row.get("pack_id") == record["pack_id"]:
                    rows[index] = record
                    break
            else:
                rows.append(record)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(self.path.suffix + ".tmp")
            with temp.open("w", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            temp.replace(self.path)
            return len(rows)


class Handler(BaseHTTPRequestHandler):
    packs: list[dict[str, Any]]
    packs_by_id: dict[str, dict[str, Any]]
    predictions: dict[str, dict[str, Any]]
    image_paths: dict[str, Path]
    store: ReviewStore
    reviewer: str
    min_excluded: int

    server_version = "PackReviewServer/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("[pack-review] " + (fmt % args) + "\n")

    def _send_json(self, value: Any, status: int = 200) -> None:
        data = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_bytes(self, data: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=300")
        self.end_headers()
        self.wfile.write(data)

    def _queue(self) -> dict[str, Any]:
        reviews = self.store.as_map()
        items = []
        for pack in self.packs:
            candidates = []
            display_files = sorted(
                pack["files"],
                key=lambda file_id: hashlib.sha256(
                    f"pack-blind-v1:{pack['id']}:{file_id}".encode("utf-8")
                ).hexdigest(),
            )
            for file_id in display_files:
                candidates.append(
                    {"file": file_id}
                )
            items.append(
                {
                    "id": pack["id"],
                    "session": pack["session"],
                    "candidates": candidates,
                    "review": reviews.get(pack["id"]),
                    "reviewed": pack["id"] in reviews,
                }
            )
        return {
            "total": len(items),
            "reviewed": sum(item["reviewed"] for item in items),
            "min_excluded": self.min_excluded,
            "items": items,
        }

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in {"/", "/index.html"}:
            self._send_bytes(PAGE_HTML.encode("utf-8"), "text/html; charset=utf-8")
            return
        if parsed.path == "/api/queue":
            self._send_json(self._queue())
            return
        if parsed.path == "/img":
            file_id = (parse_qs(parsed.query).get("file") or [""])[0]
            path = self.image_paths.get(Path(file_id).name)
            if path is None or not path.is_file():
                self._send_json({"error": "image not found"}, status=404)
                return
            self._send_bytes(
                path.read_bytes(),
                _CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream"),
            )
            return
        self._send_json({"error": "not found"}, status=404)

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/review":
            self._send_json({"error": "not found"}, status=404)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            record = self._sanitize_review(body)
            total = self.store.upsert(record)
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json({"error": str(exc)}, status=400)
            return
        except OSError as exc:
            self._send_json({"error": f"save failed: {exc}"}, status=500)
            return
        self._send_json({"ok": True, "saved": record, "reviewed": total})

    def _sanitize_review(self, body: dict[str, Any]) -> dict[str, Any]:
        pack_id = str(body.get("pack_id") or "")
        pack = self.packs_by_id.get(pack_id)
        if pack is None:
            raise ValueError("unknown pack")
        allowed = set(pack["files"])

        def unique_ids(name: str) -> list[str]:
            values = [str(value) for value in body.get(name) or []]
            if len(values) != len(set(values)) or not set(values).issubset(allowed):
                raise ValueError(f"invalid {name}")
            return values

        selected = unique_ids("selected_ids")
        excluded = unique_ids("excluded_ids")
        duplicates = unique_ids("duplicate_ids")
        if len(selected) != 5:
            raise ValueError("exactly five selected_ids are required")
        if len(excluded) < self.min_excluded:
            raise ValueError(f"at least {self.min_excluded} excluded_ids are required")
        if set(selected) & (set(excluded) | set(duplicates)):
            raise ValueError("selected files cannot also be excluded or duplicate")
        return {
            "schema_version": "pack_human_review.v1",
            "pack_id": pack_id,
            "session": pack["session"],
            "split": pack["split"],
            "selected_ids": selected,
            "excluded_ids": excluded,
            "duplicate_ids": duplicates,
            "notes": str(body.get("notes") or "")[:1000],
            "reviewer": self.reviewer,
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packs", type=Path, required=True)
    parser.add_argument(
        "--predictions",
        type=Path,
        help="optional model results; retained for compatibility and never shown",
    )
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--reviewer", default=os.environ.get("USER", "reviewer"))
    parser.add_argument("--min-excluded", type=int, default=0)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8902)
    args = parser.parse_args()

    manifest = _read_json(args.packs)
    packs = [pack for pack in manifest.get("packs") or [] if isinstance(pack, dict)]
    predictions = (
        {
            _file_id(row): row
            for row in _prediction_rows(args.predictions)
            if _file_id(row)
        }
        if args.predictions is not None
        else {}
    )
    image_paths = {
        str(file_id): Path(source)
        for pack in packs
        for file_id, source in (pack.get("source_paths") or {}).items()
    }
    missing_images = sorted(
        file_id for file_id, path in image_paths.items() if not path.is_file()
    )
    if missing_images:
        raise SystemExit(f"missing images={missing_images[:5]}")

    Handler.packs = packs
    Handler.packs_by_id = {str(pack["id"]): pack for pack in packs}
    Handler.predictions = predictions
    Handler.image_paths = image_paths
    Handler.store = ReviewStore(args.out.resolve())
    Handler.reviewer = args.reviewer
    Handler.min_excluded = max(0, args.min_excluded)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(
        f"Reviewing {len(packs)} packs / {sum(len(pack['files']) for pack in packs)} "
        f"images -> {Handler.store.path}"
    )
    print(f"Open http://{args.host}:{args.port}  (Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
    finally:
        server.server_close()
    return 0


PAGE_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pack Top-5 复核</title>
<style>
:root{color-scheme:dark;font-family:system-ui,sans-serif}*{box-sizing:border-box}
body{margin:0;background:#0d0e10;color:#eee}header{position:sticky;top:0;z-index:4;background:#111e;
padding:13px 18px;border-bottom:1px solid #333;backdrop-filter:blur(12px)}
.head{display:flex;gap:14px;align-items:center}.head h1{font-size:18px;margin:0}.progress{color:#aaa}
.bar{height:5px;background:#292b30;margin-top:9px}.bar div{height:100%;background:#46a36b;width:0}
main{padding:16px}.meta{display:flex;justify-content:space-between;margin-bottom:12px;color:#aaa}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:12px}
.card{background:#181a1e;border:1px solid #333;border-radius:9px;overflow:hidden;position:relative}
.card.selected{border-color:#4aa86f}.card.excluded{border-color:#a44752}.card.duplicate{border-color:#9a7a3f}
.card img{display:block;width:100%;aspect-ratio:3/2;object-fit:contain;background:#050505}
.body{padding:9px 10px 11px}.rank{position:absolute;top:8px;right:8px;background:#167447;color:white;
min-width:28px;text-align:center;padding:4px 6px;border-radius:99px;font-weight:700}
h2{font-size:12px;margin:0 0 6px;overflow-wrap:anywhere}.actions{display:flex;gap:5px;margin-top:8px}
button,textarea{background:#23262b;color:#eee;border:1px solid #444;border-radius:6px}
button{padding:6px 8px;cursor:pointer}.actions button{flex:1;font-size:11px}
button.on-select{background:#165d3a}button.on-exclude{background:#662631}button.on-dup{background:#655124}
.footer{display:grid;grid-template-columns:1fr auto;gap:12px;margin-top:14px}textarea{padding:8px;min-height:54px}
.nav{display:flex;gap:7px;align-items:stretch}.primary{background:#2d8150;font-weight:700}.split{font-size:11px;
padding:2px 7px;border-radius:99px;background:#292b30}.hidden{display:none}
</style></head>
<body><header><div class="head"><h1>真实场次 Pack Top-5</h1><span class="progress" id="counts"></span>
<span style="margin-left:auto" id="position"></span></div><div class="bar"><div id="bar"></div></div></header>
<main><div class="meta"><div><b id="session"></b> <span class="split">盲评</span></div>
<div>AI 分数和数据分组均已隐藏；红色=必须排除，黄色=较弱重复帧</div></div><div class="grid" id="grid"></div>
<div class="footer"><textarea id="notes" placeholder="可选备注"></textarea><div class="nav">
<button id="prev">上一组</button><button class="primary" id="save">保存并下一组</button><button id="next">跳过</button>
</div></div></main>
<script>
let ITEMS=[],idx=0,selected=[],excluded=new Set(),duplicates=new Set(),MIN_EXCLUDED=0;
const $=id=>document.getElementById(id);
async function boot(){const q=await(await fetch('/api/queue')).json();ITEMS=q.items;
MIN_EXCLUDED=Number(q.min_excluded||0);
$('counts').textContent=q.reviewed+' / '+q.total;$('bar').style.width=(100*q.reviewed/q.total)+'%';
const first=ITEMS.findIndex(x=>!x.reviewed);idx=first<0?0:first;load()}
function load(){const p=ITEMS[idx],r=p.review||{};selected=[...(r.selected_ids||[])];
excluded=new Set(r.excluded_ids||[]);duplicates=new Set(r.duplicate_ids||[]);$('notes').value=r.notes||'';
$('session').textContent=p.session;$('position').textContent=(idx+1)+' / '+ITEMS.length;
const grid=$('grid');grid.innerHTML='';p.candidates.forEach(c=>{const card=document.createElement('article');
card.className='card';card.dataset.file=c.file;const dims=c.dimensions||{};
card.innerHTML='<div class="rank"></div><img loading="lazy"><div class="body"><h2></h2><div class="actions"><button data-a="select">Top-5</button><button data-a="exclude">排除</button><button data-a="dup">重复</button></div></div>';
card.querySelector('img').src='/img?file='+encodeURIComponent(c.file);card.querySelector('h2').textContent=c.file;
card.querySelectorAll('button').forEach(b=>b.onclick=()=>toggle(c.file,b.dataset.a));grid.appendChild(card)});paint()}
function toggle(file,action){if(action==='select'){const pos=selected.indexOf(file);if(pos>=0)selected.splice(pos,1);
else{if(selected.length>=5){alert('Top-5 已满，请先取消一张');return}selected.push(file);excluded.delete(file);duplicates.delete(file)}}
if(action==='exclude'){if(excluded.has(file))excluded.delete(file);else{excluded.add(file);selected=selected.filter(x=>x!==file);duplicates.delete(file)}}
if(action==='dup'){if(duplicates.has(file))duplicates.delete(file);else{duplicates.add(file);selected=selected.filter(x=>x!==file);excluded.delete(file)}}paint()}
function paint(){document.querySelectorAll('.card').forEach(card=>{const f=card.dataset.file,pos=selected.indexOf(f);
card.classList.toggle('selected',pos>=0);card.classList.toggle('excluded',excluded.has(f));card.classList.toggle('duplicate',duplicates.has(f));
card.querySelector('.rank').textContent=pos>=0?String(pos+1):'';card.querySelector('.rank').style.display=pos>=0?'block':'none';
card.querySelector('[data-a=select]').classList.toggle('on-select',pos>=0);card.querySelector('[data-a=exclude]').classList.toggle('on-exclude',excluded.has(f));
card.querySelector('[data-a=dup]').classList.toggle('on-dup',duplicates.has(f))})}
async function save(){if(selected.length!==5){alert('请按顺序选择正好 5 张');return false}
if(excluded.size<MIN_EXCLUDED){alert('本数据集每组至少需要标记 '+MIN_EXCLUDED+' 张必须排除');return false}const p=ITEMS[idx];
const res=await fetch('/api/review',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
pack_id:p.id,selected_ids:selected,excluded_ids:[...excluded],duplicate_ids:[...duplicates],notes:$('notes').value||''})});
if(!res.ok){const e=await res.json();alert(e.error||'保存失败');return false}p.review=(await res.json().catch(()=>({}))).saved;p.reviewed=true;
const done=ITEMS.filter(x=>x.reviewed).length;$('counts').textContent=done+' / '+ITEMS.length;$('bar').style.width=(100*done/ITEMS.length)+'%';return true}
function go(d){idx=(idx+d+ITEMS.length)%ITEMS.length;load()}
$('save').onclick=async()=>{if(await save())go(1)};$('prev').onclick=()=>go(-1);$('next').onclick=()=>go(1);
document.addEventListener('keydown',async e=>{if(e.key==='Enter'&&e.target.tagName!=='TEXTAREA'){e.preventDefault();if(await save())go(1)}
else if(e.key==='ArrowLeft')go(-1);else if(e.key==='ArrowRight')go(1)});boot();
</script></body></html>"""


if __name__ == "__main__":
    raise SystemExit(main())
