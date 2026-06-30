#!/usr/bin/env python3
"""Minimal, dependency-free web UI for long-term Stage3 ground-truth labeling.

Standard-library only (``http.server``) — no FastAPI/uvicorn/Next build needed.
Serves images from a folder and persists labels to the same JSONL the eval
harness reads, so labeling closes the loop with ``scripts/eval_stage3.py``.

Run::

    python scripts/label_server.py --images /path/to/Previews \\
        --labels data/eval/labels.jsonl --predictions analysis_results.json

Then open http://127.0.0.1:8900 . Labels autosave on navigation; resume any time.

Label schema (one JSON object per JSONL line)::

    {"file": "DSC0001.jpg", "overall": 85,
     "dims": {"focus_sharpness": 8, ...}, "keep": true, "notes": "..."}
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.eval.labels import DIM_KEYS, normalize_name
from utils.stage3_dimensions import STAGE3_DIM_LABELS

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
_CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
}

DIM_META = [{"key": k, "label": STAGE3_DIM_LABELS.get(k, k)} for k in DIM_KEYS]

_write_lock = threading.Lock()


class LabelStore:
    """JSONL-backed label store with filename-normalized upsert."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def read_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: list[dict[str, Any]] = []
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return rows

    def as_map(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for r in self.read_all():
            f = r.get("file") or r.get("path")
            if f:
                out[normalize_name(str(f))] = r
        return out

    def upsert(self, record: dict[str, Any]) -> int:
        key = normalize_name(str(record.get("file") or ""))
        if not key:
            raise ValueError("record missing 'file'")
        with _write_lock:
            rows = self.read_all()
            replaced = False
            for i, r in enumerate(rows):
                if normalize_name(str(r.get("file") or "")) == key:
                    rows[i] = record
                    replaced = True
                    break
            if not replaced:
                rows.append(record)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                for r in rows:
                    fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            tmp.replace(self.path)
            return len(rows)


def _index_images(images_dir: Path) -> dict[str, Path]:
    """basename(lower) -> absolute path, recursive scan."""
    index: dict[str, Path] = {}
    for p in sorted(images_dir.rglob("*")):
        if p.is_file() and p.suffix.lower() in _IMAGE_EXTS:
            index.setdefault(p.name.lower(), p.resolve())
    return index


def _clamp(v: Any, lo: float, hi: float) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return max(lo, min(hi, x))


def _sanitize_record(body: dict[str, Any]) -> dict[str, Any]:
    file = str(body.get("file") or "").strip()
    if not file:
        raise ValueError("missing file")
    overall = _clamp(body.get("overall"), 0, 100)
    dims_in = body.get("dims") or {}
    dims: dict[str, Any] = {}
    if isinstance(dims_in, dict):
        for k in DIM_KEYS:
            v = _clamp(dims_in.get(k), 0, 10)
            dims[k] = None if v is None else round(v, 1)
    keep = body.get("keep")
    keep_val = bool(keep) if isinstance(keep, bool) else None
    notes = str(body.get("notes") or "")
    return {
        "file": Path(file).name,
        "overall": None if overall is None else round(overall, 1),
        "dims": dims,
        "keep": keep_val,
        "notes": notes,
    }


class Handler(BaseHTTPRequestHandler):
    images_dir: Path
    store: LabelStore
    predictions_map: dict[str, dict[str, Any]]
    image_index: dict[str, Path]

    server_version = "LabelServer/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:  # quieter logs
        sys.stderr.write("[label] " + (fmt % args) + "\n")

    # --- helpers ---
    def _send_json(self, obj: Any, status: int = 200) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_bytes(self, data: bytes, content_type: str, cache: str = "public, max-age=300") -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(data)

    def _build_queue(self) -> list[dict[str, Any]]:
        labels = self.store.as_map()
        items: list[dict[str, Any]] = []
        for name in sorted(self.image_index.keys()):
            disp = self.image_index[name].name
            key = normalize_name(disp)
            lab = labels.get(key)
            pred = self.predictions_map.get(key)
            ai = None
            if pred is not None:
                ai = {"overall": pred.get("overall"), "dims": pred.get("dims_cal") or {}}
            items.append(
                {
                    "file": disp,
                    "labeled": lab is not None and lab.get("overall") is not None,
                    "label": lab,
                    "ai": ai,
                }
            )
        return items

    # --- routes ---
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        route = parsed.path
        if route == "/" or route == "/index.html":
            self._send_bytes(PAGE_HTML.encode("utf-8"), "text/html; charset=utf-8", cache="no-store")
            return
        if route == "/api/meta":
            self._send_json({"dims": DIM_META, "images_dir": str(self.images_dir), "labels_path": str(self.store.path)})
            return
        if route == "/api/queue":
            items = self._build_queue()
            labeled = sum(1 for it in items if it["labeled"])
            self._send_json({"total": len(items), "labeled": labeled, "items": items})
            return
        if route == "/img":
            qs = parse_qs(parsed.query)
            fname = (qs.get("file") or [""])[0]
            self._serve_image(fname)
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
        ctype = _CONTENT_TYPES.get(p.suffix.lower(), "application/octet-stream")
        self._send_bytes(data, ctype)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/label":
            self._send_json({"error": "not found"}, status=404)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length > 0 else b"{}"
            body = json.loads(raw.decode("utf-8"))
            record = _sanitize_record(body)
            total = self.store.upsert(record)
        except (ValueError, json.JSONDecodeError) as e:
            self._send_json({"error": str(e)}, status=400)
            return
        except Exception as e:  # noqa: BLE001
            self._send_json({"error": f"save failed: {e}"}, status=500)
            return
        self._send_json({"ok": True, "saved": record, "total_labels": total})


def _load_predictions_map(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    try:
        from scripts.eval.labels import load_predictions

        preds = load_predictions(path)
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[label] predictions load failed ({e}); continuing without AI reference\n")
        return {}
    out: dict[str, dict[str, Any]] = {}
    for p in preds:
        if p.key:
            out.setdefault(p.key, {"overall": p.overall, "dims_cal": p.dims_cal})
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Minimal Stage3 labeling web server (stdlib only)")
    parser.add_argument("--images", required=True, help="directory of images to label (recursive)")
    parser.add_argument("--labels", default="data/eval/labels.jsonl", help="JSONL labels file (default: %(default)s)")
    parser.add_argument("--predictions", default="analysis_results.json", help="optional AI predictions for reference")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8900)
    args = parser.parse_args(argv)

    images_dir = Path(args.images).expanduser().resolve()
    if not images_dir.is_dir():
        sys.stderr.write(f"images dir not found: {images_dir}\n")
        return 2
    image_index = _index_images(images_dir)
    if not image_index:
        sys.stderr.write(f"no images found under {images_dir}\n")
        return 2

    preds_path = Path(args.predictions).expanduser() if args.predictions else None
    predictions_map = _load_predictions_map(preds_path)

    Handler.images_dir = images_dir
    Handler.store = LabelStore(Path(args.labels).expanduser().resolve())
    Handler.predictions_map = predictions_map
    Handler.image_index = image_index

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}"
    print(f"Labeling {len(image_index)} images from {images_dir}")
    print(f"Labels -> {Handler.store.path}")
    print(f"AI reference: {'on' if predictions_map else 'off'}")
    print(f"Open {url}  (Ctrl-C to stop)")
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
<title>Stage3 标注台</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin:0; font-family: system-ui,-apple-system,"PingFang SC",sans-serif; background:#0b0b0d; color:#e7e7ea; }
  header { display:flex; align-items:center; gap:1rem; padding:.6rem 1rem; border-bottom:1px solid #1d1d22; position:sticky; top:0; background:#0b0b0d; z-index:5; }
  header h1 { font-size:.95rem; font-weight:600; margin:0; letter-spacing:.02em; }
  .progress { flex:1; height:6px; background:#1d1d22; border-radius:99px; overflow:hidden; max-width:340px; }
  .progress > div { height:100%; background:linear-gradient(90deg,#34d399,#10b981); width:0%; transition:width .3s; }
  .muted { color:#8a8a93; font-size:.8rem; }
  main { display:grid; grid-template-columns: minmax(0,1.4fr) minmax(320px,.85fr); gap:1px; height:calc(100vh - 49px); }
  .stage { background:#000; display:flex; align-items:center; justify-content:center; overflow:hidden; position:relative; }
  .stage img { max-width:100%; max-height:100%; object-fit:contain; }
  .stage .fname { position:absolute; left:0; right:0; bottom:0; padding:.4rem .7rem; font-size:.75rem; background:linear-gradient(transparent,#000a); color:#cfcfd6; }
  .panel { background:#0e0e12; padding:1rem 1.1rem; overflow-y:auto; }
  .row { margin-bottom:.85rem; }
  label.fld { display:block; font-size:.72rem; text-transform:uppercase; letter-spacing:.08em; color:#8a8a93; margin-bottom:.3rem; }
  input[type=number] { width:100%; background:#141419; border:1px solid #26262e; color:#e7e7ea; border-radius:8px; padding:.5rem .6rem; font-size:1rem; }
  input[type=number]:focus { outline:none; border-color:#10b981; }
  .dims { display:grid; grid-template-columns:1fr 1fr; gap:.55rem; }
  .dim { background:#121217; border:1px solid #21212a; border-radius:8px; padding:.45rem .55rem; }
  .dim .dl { display:flex; justify-content:space-between; align-items:baseline; }
  .dim .dl span:first-child { font-size:.82rem; }
  .dim .ai { font-size:.68rem; color:#6b6b73; }
  .dim input { margin-top:.3rem; padding:.35rem .45rem; font-size:.95rem; }
  .keep { display:flex; gap:.5rem; }
  .keep button { flex:1; padding:.55rem; border-radius:8px; border:1px solid #2a2a33; background:#141419; color:#cfcfd6; cursor:pointer; font-size:.85rem; }
  .keep button.on-keep { background:#064e3b; border-color:#10b981; color:#a7f3d0; }
  .keep button.on-drop { background:#4c0519; border-color:#f43f5e; color:#fecdd3; }
  textarea { width:100%; background:#141419; border:1px solid #26262e; color:#e7e7ea; border-radius:8px; padding:.5rem .6rem; resize:vertical; min-height:48px; font-family:inherit; font-size:.9rem; }
  .nav { display:flex; gap:.5rem; margin-top:.4rem; }
  .nav button { flex:1; padding:.6rem; border-radius:8px; border:1px solid #2a2a33; background:#141419; color:#e7e7ea; cursor:pointer; font-size:.9rem; }
  .nav button.primary { background:#10b981; border-color:#10b981; color:#04231a; font-weight:600; }
  .nav button:hover { filter:brightness(1.12); }
  .airef { font-size:.74rem; color:#7a7a83; margin-top:.2rem; }
  .hint { font-size:.7rem; color:#6b6b73; margin-top:.8rem; line-height:1.5; }
  .filterbar { display:flex; align-items:center; gap:.4rem; font-size:.78rem; color:#a0a0a8; }
  .filterbar input { width:auto; }
  .pill { font-size:.7rem; padding:.1rem .45rem; border-radius:99px; background:#1d1d22; color:#9a9aa3; }
  .pill.done { background:#064e3b; color:#a7f3d0; }
</style>
</head>
<body>
<header>
  <h1>Stage3 标注台</h1>
  <div class="progress"><div id="bar"></div></div>
  <span class="muted" id="counts">0 / 0</span>
  <span class="muted" id="pos" style="margin-left:auto"></span>
  <label class="filterbar"><input type="checkbox" id="onlyUnlabeled" checked> 只看未标注</label>
</header>
<main>
  <div class="stage">
    <img id="photo" alt="" />
    <div class="fname" id="fname"></div>
  </div>
  <div class="panel">
    <div class="row">
      <label class="fld">整体分 Overall (0–100)</label>
      <input type="number" id="overall" min="0" max="100" step="1" />
      <div class="airef" id="aiOverall"></div>
    </div>
    <div class="row">
      <label class="fld">保留 Keep</label>
      <div class="keep">
        <button type="button" id="btnKeep">★ 保留 (K)</button>
        <button type="button" id="btnDrop">✕ 弃 (D)</button>
      </div>
    </div>
    <div class="row">
      <label class="fld">维度分 (0–10，可留空)</label>
      <div class="dims" id="dims"></div>
    </div>
    <div class="row">
      <label class="fld">备注</label>
      <textarea id="notes" placeholder="可选"></textarea>
    </div>
    <div class="nav">
      <button type="button" id="prev">← 上一张</button>
      <button type="button" class="primary" id="saveNext">保存并下一张 (Enter)</button>
      <button type="button" id="next">跳过 →</button>
    </div>
    <div class="hint">
      快捷键：Enter 保存并下一张 · ←/→ 切换 · K 保留 · D 弃 · 数字框可留空（不计入该维度）。<br>
      标注自动写入 JSONL，可随时关闭后继续。完成后运行：<br>
      <code>python scripts/eval_stage3.py run --labels &lt;labels.jsonl&gt; --predictions analysis_results.json</code>
    </div>
  </div>
</main>
<script>
let DIMS = [];
let ITEMS = [];
let idx = 0;

const $ = (id) => document.getElementById(id);

async function boot() {
  const meta = await (await fetch('/api/meta')).json();
  DIMS = meta.dims;
  buildDimInputs();
  await refreshQueue(true);
  bindKeys();
}

function buildDimInputs() {
  const wrap = $('dims');
  wrap.innerHTML = '';
  for (const d of DIMS) {
    const el = document.createElement('div');
    el.className = 'dim';
    el.innerHTML = `<div class="dl"><span>${d.label}</span><span class="ai" id="ai_${d.key}"></span></div>
      <input type="number" min="0" max="10" step="0.5" id="dim_${d.key}" />`;
    wrap.appendChild(el);
  }
}

async function refreshQueue(keepPos) {
  const data = await (await fetch('/api/queue')).json();
  ITEMS = data.items;
  $('bar').style.width = (data.total ? (100*data.labeled/data.total) : 0) + '%';
  $('counts').textContent = data.labeled + ' / ' + data.total;
  if (!keepPos) return;
  // jump to first unlabeled
  const first = ITEMS.findIndex(it => !it.labeled);
  idx = first >= 0 ? first : 0;
  load();
}

function visibleNextIndex(from, dir) {
  const onlyUn = $('onlyUnlabeled').checked;
  let i = from;
  for (let step = 0; step < ITEMS.length; step++) {
    i = (i + dir + ITEMS.length) % ITEMS.length;
    if (!onlyUn || !ITEMS[i].labeled) return i;
  }
  return from;
}

function load() {
  if (!ITEMS.length) return;
  const it = ITEMS[idx];
  $('photo').src = '/img?file=' + encodeURIComponent(it.file) + '&t=' + Date.now();
  $('fname').textContent = it.file + '   (' + (idx+1) + '/' + ITEMS.length + ')';
  const lab = it.label || {};
  $('overall').value = (lab.overall ?? '');
  $('notes').value = lab.notes || '';
  setKeep(lab.keep);
  const aiO = it.ai && it.ai.overall != null ? ('AI: ' + it.ai.overall) : '';
  $('aiOverall').textContent = aiO;
  for (const d of DIMS) {
    const v = lab.dims ? lab.dims[d.key] : null;
    $('dim_'+d.key).value = (v ?? '');
    const av = it.ai && it.ai.dims ? it.ai.dims[d.key] : null;
    $('ai_'+d.key).textContent = (av != null ? 'AI '+av : '');
  }
  $('pos').textContent = it.labeled ? '已标注' : '未标注';
}

let keepState = null;
function setKeep(v) {
  keepState = (typeof v === 'boolean') ? v : null;
  $('btnKeep').classList.toggle('on-keep', keepState === true);
  $('btnDrop').classList.toggle('on-drop', keepState === false);
}

function collect() {
  const dims = {};
  for (const d of DIMS) {
    const raw = $('dim_'+d.key).value;
    dims[d.key] = raw === '' ? null : Number(raw);
  }
  const ov = $('overall').value;
  return {
    file: ITEMS[idx].file,
    overall: ov === '' ? null : Number(ov),
    dims,
    keep: keepState,
    notes: $('notes').value || ''
  };
}

function hasInput(rec) {
  if (rec.overall != null) return true;
  if (rec.keep != null) return true;
  if (rec.notes) return true;
  return Object.values(rec.dims).some(v => v != null);
}

async function save() {
  if (!ITEMS.length) return false;
  const rec = collect();
  if (!hasInput(rec)) return false; // nothing to save
  const res = await fetch('/api/label', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify(rec)
  });
  if (!res.ok) { alert('保存失败'); return false; }
  // update local cache
  ITEMS[idx].label = rec;
  ITEMS[idx].labeled = rec.overall != null;
  const labeled = ITEMS.filter(i=>i.labeled).length;
  $('bar').style.width = (100*labeled/ITEMS.length) + '%';
  $('counts').textContent = labeled + ' / ' + ITEMS.length;
  return true;
}

async function saveAndNext() {
  await save();
  idx = visibleNextIndex(idx, +1);
  load();
}
function go(dir) { idx = visibleNextIndex(idx, dir); load(); }

function bindKeys() {
  $('saveNext').onclick = saveAndNext;
  $('prev').onclick = async () => { await save(); go(-1); };
  $('next').onclick = () => go(+1);
  $('btnKeep').onclick = () => setKeep(keepState === true ? null : true);
  $('btnDrop').onclick = () => setKeep(keepState === false ? null : false);
  $('onlyUnlabeled').onchange = () => {};
  document.addEventListener('keydown', (e) => {
    const tag = (e.target.tagName || '').toLowerCase();
    const typing = tag === 'textarea' || (tag === 'input' && e.target.type !== 'checkbox');
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); saveAndNext(); return; }
    if (typing) return;
    if (e.key === 'ArrowRight') { e.preventDefault(); go(+1); }
    else if (e.key === 'ArrowLeft') { e.preventDefault(); go(-1); }
    else if (e.key.toLowerCase() === 'k') { setKeep(keepState === true ? null : true); }
    else if (e.key.toLowerCase() === 'd') { setKeep(keepState === false ? null : false); }
  });
}

boot();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
