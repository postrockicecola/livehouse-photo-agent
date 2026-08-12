#!/usr/bin/env python3
"""Build a local thumbnail gallery for a candidate-round JSONL manifest."""
from __future__ import annotations

import argparse
import html
import json
from collections import Counter
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps


CATEGORY_LABELS = {
    "technical_hard": "技术硬伤候选",
    "semantic_defect": "语义瑕疵候选",
    "ordinary": "普通对照候选",
    "highlight": "高光候选",
}


def load_candidates(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON ({exc})") from exc
            if isinstance(row, dict) and row.get("file") and row.get("source_path"):
                rows.append(row)
    return rows


def _build_thumbnail(source: Path, destination: Path, max_side: int) -> None:
    with Image.open(source) as image:
        image = ImageOps.exif_transpose(image)
        image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        if image.mode != "RGB":
            image = image.convert("RGB")
        image.save(destination, "JPEG", quality=82, optimize=True)


def _card(row: dict[str, Any], thumbnail_name: str, index: int) -> str:
    category = str(row.get("target_category") or "unknown")
    historical = row.get("historical") or {}
    reasons = row.get("selection_reasons") or []
    reason_text = " · ".join(str(reason) for reason in reasons)
    score = row.get("mining_score")
    overall = historical.get("overall_score")
    metadata = [
        f"挖掘分 {float(score):.1f}" if isinstance(score, (int, float)) else "",
        f"历史总分 {float(overall):.1f}" if isinstance(overall, (int, float)) else "",
        str(row.get("session") or ""),
    ]
    return f"""
      <article class="card" data-category="{html.escape(category)}"
               data-search="{html.escape(str(row['file']).casefold())}">
        <a href="{html.escape(str(row['source_path']))}" target="_blank">
          <img src="gallery_assets/{html.escape(thumbnail_name)}"
               alt="{html.escape(str(row['file']))}" loading="lazy">
        </a>
        <div class="body">
          <div class="number">#{index:03d}</div>
          <h2 title="{html.escape(str(row['file']))}">{html.escape(str(row['file']))}</h2>
          <span class="badge">{html.escape(CATEGORY_LABELS.get(category, category))}</span>
          <p class="meta">{html.escape(" · ".join(item for item in metadata if item))}</p>
          <p class="reason">{html.escape(reason_text)}</p>
        </div>
      </article>"""


def build_gallery(manifest_path: Path, output_path: Path, max_side: int = 480) -> int:
    rows = load_candidates(manifest_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    assets = output_path.parent / "gallery_assets"
    assets.mkdir(parents=True, exist_ok=True)

    expected_thumbnails = {
        f"{index:03d}_{Path(str(row['file'])).stem}.jpg"
        for index, row in enumerate(rows, start=1)
    }
    for existing in assets.glob("*.jpg"):
        if existing.name not in expected_thumbnails:
            existing.unlink()

    cards: list[str] = []
    missing: list[str] = []
    for index, row in enumerate(rows, start=1):
        source = Path(str(row["source_path"]))
        thumbnail_name = f"{index:03d}_{Path(str(row['file'])).stem}.jpg"
        destination = assets / thumbnail_name
        if source.is_file():
            if not destination.is_file():
                _build_thumbnail(source, destination, max_side)
        else:
            missing.append(str(source))
            continue
        cards.append(_card(row, thumbnail_name, index))

    counts = Counter(str(row.get("target_category") or "unknown") for row in rows)
    filter_buttons = "\n".join(
        f'<button data-filter="{html.escape(category)}">'
        f"{html.escape(CATEGORY_LABELS.get(category, category))} ({count})</button>"
        for category, count in counts.items()
    )
    document = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Selection Candidate Round · {len(rows)} photos</title>
  <style>
    :root {{ color-scheme: dark; font-family: system-ui, sans-serif; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: #111; color: #eee; }}
    header {{ position: sticky; top: 0; z-index: 2; padding: 16px 20px;
              background: rgba(17,17,17,.96); border-bottom: 1px solid #333; }}
    h1 {{ margin: 0 0 6px; font-size: 22px; }}
    .notice, .meta, .reason {{ color: #aaa; font-size: 13px; }}
    .controls {{ display: flex; gap: 8px; flex-wrap: wrap; margin-top: 12px; }}
    input, button {{ border: 1px solid #444; border-radius: 7px; background: #222;
                     color: #eee; padding: 8px 11px; }}
    input {{ min-width: 240px; }}
    button.active {{ background: #eee; color: #111; }}
    main {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr));
            gap: 14px; padding: 18px; }}
    .card {{ min-width: 0; overflow: hidden; border: 1px solid #333;
             border-radius: 9px; background: #191919; }}
    .card img {{ display: block; width: 100%; aspect-ratio: 3 / 2;
                 object-fit: contain; background: #080808; }}
    .body {{ padding: 10px 12px 13px; }}
    .number {{ float: right; color: #777; font-size: 12px; }}
    h2 {{ margin: 0 36px 8px 0; font-size: 14px; white-space: nowrap;
          overflow: hidden; text-overflow: ellipsis; }}
    .badge {{ display: inline-block; padding: 3px 7px; border-radius: 999px;
              background: #303030; font-size: 12px; }}
    .meta {{ margin: 9px 0 0; }}
    .reason {{ min-height: 32px; margin: 7px 0 0; line-height: 1.3; }}
    .hidden {{ display: none; }}
  </style>
</head>
<body>
  <header>
    <h1>第一轮候选 · {len(rows)} 张</h1>
    <div class="notice">类别为历史模型驱动的抽样目标，不是最终人工标签。</div>
    <div class="controls">
      <input id="search" placeholder="搜索文件名">
      <button class="active" data-filter="all">全部 ({len(rows)})</button>
      {filter_buttons}
    </div>
  </header>
  <main>
    {''.join(cards)}
  </main>
  <script>
    const cards = [...document.querySelectorAll('.card')];
    const search = document.querySelector('#search');
    let filter = 'all';
    function apply() {{
      const query = search.value.trim().toLowerCase();
      cards.forEach(card => {{
        const categoryOK = filter === 'all' || card.dataset.category === filter;
        const searchOK = !query || card.dataset.search.includes(query);
        card.classList.toggle('hidden', !(categoryOK && searchOK));
      }});
    }}
    search.addEventListener('input', apply);
    document.querySelectorAll('button[data-filter]').forEach(button => {{
      button.addEventListener('click', () => {{
        document.querySelectorAll('button').forEach(item => item.classList.remove('active'));
        button.classList.add('active');
        filter = button.dataset.filter;
        apply();
      }});
    }});
  </script>
</body>
</html>
"""
    output_path.write_text(document, encoding="utf-8")
    files_path = output_path.parent / "files.txt"
    files_path.write_text(
        "\n".join(str(row["file"]) for row in rows) + "\n",
        encoding="utf-8",
    )
    if missing:
        raise FileNotFoundError(f"{len(missing)} candidate source images are missing")
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-side", type=int, default=480)
    args = parser.parse_args()
    output = args.output or args.manifest.with_name("gallery.html")
    count = build_gallery(args.manifest, output, max(160, args.max_side))
    print(f"Wrote {count} candidates to {output}")
    print(f"File list: {output.parent / 'files.txt'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
