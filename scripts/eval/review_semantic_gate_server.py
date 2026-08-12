#!/usr/bin/env python3
"""Blind local review UI for semantic-gate development labels."""
from __future__ import annotations

import argparse
import io
import json
import mimetypes
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from PIL import Image, ImageOps


DISPOSITIONS = {"pass", "semantic_reject", "technical_reject", "out_of_domain"}
DEFECT_TYPES = {
    "closed_eyes",
    "heavy_occlusion",
    "no_clear_subject",
    "missed_moment",
    "severe_composition_failure",
    "bad_expression",
    "invalid_pose",
    "other",
}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.is_file():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON") from exc
            if isinstance(row, dict):
                rows.append(row)
    return rows


def normalize_review(body: dict[str, Any], allowed_files: set[str]) -> dict[str, Any]:
    file_id = Path(str(body.get("file") or "")).name
    if file_id not in allowed_files:
        raise ValueError("unknown file")
    disposition = str(body.get("disposition") or "")
    if disposition not in DISPOSITIONS:
        raise ValueError("invalid disposition")
    raw_types = body.get("types") or []
    types = list(
        dict.fromkeys(
            str(value)
            for value in raw_types
            if str(value) in DEFECT_TYPES
        )
    )
    if disposition == "semantic_reject" and not types:
        raise ValueError("semantic_reject requires at least one defect type")
    try:
        severity = max(0, min(3, int(body.get("severity") or 0)))
    except (TypeError, ValueError):
        severity = 0
    if disposition == "semantic_reject" and severity < 2:
        raise ValueError("semantic_reject severity must be 2 or 3")
    return {
        "schema_version": "semantic_gate_review.v1",
        "file": file_id,
        "disposition": disposition,
        "semantic_gate": {
            "is_present": disposition == "semantic_reject",
            "types": types if disposition == "semantic_reject" else [],
            "severity": severity if disposition == "semantic_reject" else 0,
            "evidence": str(body.get("evidence") or "").strip()[:1000],
        },
        "ai_revealed": bool(body.get("ai_revealed")),
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
    }


class ReviewState:
    def __init__(
        self,
        suggestions: Path,
        images: Path,
        output: Path,
        orientation_review: Path | None = None,
    ) -> None:
        rows = _read_jsonl(suggestions)
        self.order = [str(row["file"]) for row in rows]
        self.suggestions = {str(row["file"]): row for row in rows}
        self.images = images.resolve()
        self.output = output
        self.orientation_review = orientation_review
        self.reviews = {
            str(row["file"]): row for row in _read_jsonl(output) if row.get("file")
        }

    def image_bytes(self, file_id: str) -> bytes:
        image_path = self.images / file_id
        degrees = 0
        if self.orientation_review and self.orientation_review.is_file():
            raw = json.loads(self.orientation_review.read_text(encoding="utf-8"))
            item = (raw.get("items") or {}).get(file_id) or {}
            degrees = int(item.get("rotation_degrees") or 0)
        with Image.open(image_path) as image:
            normalized = ImageOps.exif_transpose(image)
            if degrees:
                normalized = normalized.rotate(-degrees, expand=True)
            if normalized.mode != "RGB":
                normalized = normalized.convert("RGB")
            buffer = io.BytesIO()
            normalized.save(buffer, "JPEG", quality=92)
            return buffer.getvalue()

    def save(self, review: dict[str, Any]) -> None:
        self.output.parent.mkdir(parents=True, exist_ok=True)
        with self.output.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(review, ensure_ascii=False) + "\n")
        self.reviews[str(review["file"])] = review


class Handler(BaseHTTPRequestHandler):
    state: ReviewState

    def _json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/":
            body = HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/api/items":
            self._json(
                [
                    {
                        "file": file_id,
                        "review": self.state.reviews.get(file_id),
                        "ai": {
                            "overall": self.state.suggestions[file_id].get("overall"),
                            "semantic_defect": self.state.suggestions[file_id].get(
                                "semantic_defect"
                            ),
                            "reason": self.state.suggestions[file_id].get("reason"),
                        },
                    }
                    for file_id in self.state.order
                ]
            )
            return
        if parsed.path == "/api/ai":
            file_id = Path(parse_qs(parsed.query).get("file", [""])[0]).name
            suggestion = self.state.suggestions.get(file_id)
            self._json(suggestion or {}, 200 if suggestion else 404)
            return
        if parsed.path == "/img":
            file_id = Path(parse_qs(parsed.query).get("file", [""])[0]).name
            if file_id not in self.state.suggestions:
                self.send_error(404)
                return
            image = self.state.images / file_id
            if image.parent.resolve() != self.state.images or not image.is_file():
                self.send_error(404)
                return
            body = self.state.image_bytes(file_id)
            self.send_response(200)
            self.send_header(
                "Content-Type",
                mimetypes.guess_type(image.name)[0] or "image/jpeg",
            )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/api/review":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("JSON object required")
            review = normalize_review(body, set(self.state.order))
            self.state.save(review)
            self._json({"ok": True, "review": review})
        except (ValueError, json.JSONDecodeError) as exc:
            self._json({"ok": False, "error": str(exc)}, 400)

    def log_message(self, format: str, *args: Any) -> None:
        print(f'[semantic-review] {format % args}')


HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Semantic Gate AI-assisted Review</title>
<style>
:root{color-scheme:dark;font-family:system-ui,sans-serif}*{box-sizing:border-box}
body{margin:0;background:#0d0e11;color:#eee}header{position:sticky;top:0;z-index:2;
display:flex;gap:14px;align-items:center;padding:12px 18px;background:#15161beF;border-bottom:1px solid #333}
h1{font-size:18px;margin:0}#progress{color:#aaa}main{display:grid;grid-template-columns:minmax(0,2fr) minmax(330px,1fr);
gap:16px;padding:16px;height:calc(100vh - 60px)}.image{display:flex;align-items:center;justify-content:center;
min-height:0;background:#050505;border-radius:10px;overflow:hidden}.image img{max-width:100%;max-height:100%;object-fit:contain}
aside{overflow:auto;background:#17191e;border:1px solid #333;border-radius:10px;padding:15px}
h2{font-size:14px;overflow-wrap:anywhere}.choice{display:grid;grid-template-columns:1fr 1fr;gap:8px}
button{padding:10px;border:1px solid #444;border-radius:8px;background:#24262c;color:#eee;cursor:pointer}
button.on{outline:2px solid #60a5fa;background:#18375c}.types{display:grid;gap:7px;margin:14px 0}
label{font-size:13px}.field{display:block;margin-top:12px;color:#aaa}select,textarea{width:100%;margin-top:5px;
background:#202228;color:#eee;border:1px solid #444;border-radius:7px;padding:8px}textarea{min-height:80px}
.ai{white-space:pre-wrap;background:#111318;border:1px solid #333;padding:10px;border-radius:8px;color:#bbb;font-size:12px}
.nav{display:flex;gap:8px;margin-top:14px}.nav button{flex:1}.save{background:#14532d}
@media(max-width:850px){main{grid-template-columns:1fr;height:auto}.image{height:55vh}}
</style></head><body>
<header><h1>语义门禁 AI 辅助复核</h1><span id="progress"></span><span>AI 已预填，请确认或修改</span></header>
<main><div class="image"><img id="image"></div><aside>
<h2 id="file"></h2><div class="choice">
<button data-d="pass">P · 语义通过</button><button data-d="semantic_reject">S · 语义废片</button>
<button data-d="technical_reject">T · 技术废片</button><button data-d="out_of_domain">O · 非目标域</button>
</div><div class="types" id="types"></div>
<label class="field">严重度<select id="severity"><option value="0">0</option><option value="1">1</option>
<option value="2">2 · material</option><option value="3">3 · unusable</option></select></label>
<label class="field">可见证据<textarea id="evidence" placeholder="只描述画面中可见的问题"></textarea></label>
<div class="nav"><button class="save" id="save">保存并下一张</button></div>
<pre class="ai" id="ai"></pre>
<div class="nav"><button id="prev">← 上一张</button><button id="next">下一张 →</button></div>
</aside></main>
<script>
const TYPE_LABELS={closed_eyes:'闭眼',heavy_occlusion:'严重遮挡',no_clear_subject:'无明确主体',
missed_moment:'错失瞬间',severe_composition_failure:'严重构图失败',bad_expression:'表情失败',
invalid_pose:'无效姿态',other:'其他'};
let items=[],idx=0,disposition=null,revealed=true;
const $=id=>document.getElementById(id);
async function init(){items=await (await fetch('/api/items')).json();buildTypes();
 const first=items.findIndex(x=>!x.review);idx=first<0?0:first;render();}
function buildTypes(){$('types').innerHTML=Object.entries(TYPE_LABELS).map(([k,v])=>
 `<label><input type="checkbox" value="${k}"> ${v}</label>`).join('');}
function choose(value){disposition=value;document.querySelectorAll('[data-d]').forEach(
 b=>b.classList.toggle('on',b.dataset.d===value));}
function severityValue(value){return ({none:0,minor:1,major:2,fatal:3})[value] ?? Number(value||0)}
function render(){const it=items[idx],rv=it.review||null,ai=it.ai||{},aiGate=ai.semantic_defect||{};
 revealed=true;$('ai').textContent=JSON.stringify(ai,null,2);
 $('file').textContent=it.file;$('image').src='/img?file='+encodeURIComponent(it.file);
 choose(rv?.disposition||(aiGate.is_present?'semantic_reject':'pass'));
 const gate=rv?.semantic_gate||aiGate;
 document.querySelectorAll('#types input').forEach(x=>x.checked=(gate.types||[]).includes(x.value));
 $('severity').value=String(severityValue(gate.severity));$('evidence').value=gate.evidence||'';
 $('progress').textContent=`${idx+1}/${items.length} · 已完成 ${items.filter(x=>x.review).length}`;}
async function save(){if(!disposition){alert('请选择判断');return}
 const types=[...document.querySelectorAll('#types input:checked')].map(x=>x.value);
 const body={file:items[idx].file,disposition,types,severity:Number($('severity').value),
 evidence:$('evidence').value,ai_revealed:revealed};
 const r=await fetch('/api/review',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
 const data=await r.json();if(!r.ok){alert(data.error);return}items[idx].review=data.review;
 if(idx<items.length-1)idx++;render();}
function go(delta){idx=Math.max(0,Math.min(items.length-1,idx+delta));render()}
document.querySelectorAll('[data-d]').forEach(b=>b.onclick=()=>choose(b.dataset.d));
$('save').onclick=save;$('prev').onclick=()=>go(-1);$('next').onclick=()=>go(1);
document.addEventListener('keydown',e=>{if(e.target.matches('textarea,input,select'))return;
 const key=e.key.toLowerCase();if(key==='p')choose('pass');if(key==='s')choose('semantic_reject');
 if(key==='t')choose('technical_reject');if(key==='o')choose('out_of_domain');
 if(key==='enter')save();if(key==='arrowleft')go(-1);if(key==='arrowright')go(1);});
init();
</script></body></html>"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suggestions", type=Path, required=True)
    parser.add_argument("--images", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--orientation-review", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8902)
    args = parser.parse_args()
    Handler.state = ReviewState(
        args.suggestions,
        args.images,
        args.out,
        args.orientation_review,
    )
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Semantic review: http://{args.host}:{args.port}")
    print(f"Items: {len(Handler.state.order)}, reviewed: {len(Handler.state.reviews)}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
