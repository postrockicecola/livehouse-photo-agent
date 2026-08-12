#!/usr/bin/env python3
"""Build a static review gallery by joining evaluation errors, labels, and predictions."""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any

from PIL import Image


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON") from exc
    return rows


def _thumbnail(source: Path, destination: Path, max_side: int) -> None:
    with Image.open(source) as image:
        image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        if image.mode != "RGB":
            image = image.convert("RGB")
        image.save(destination, "JPEG", quality=84, optimize=True)


def build_gallery(
    *,
    errors_path: Path,
    labels_path: Path,
    predictions_path: Path,
    images_dir: Path,
    output_path: Path,
    sample_type: str,
    error_type: str,
    max_side: int = 640,
) -> int:
    errors = [
        row
        for row in _read_jsonl(errors_path)
        if row.get("sample_type") == sample_type
        and row.get("error_type") == error_type
    ]
    labels = {str(row["file"]): row for row in _read_jsonl(labels_path)}
    predictions = {
        str(row["file"]): row for row in _read_jsonl(predictions_path)
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    assets = output_path.parent / "assets"
    assets.mkdir(exist_ok=True)

    cards: list[str] = []
    for index, error in enumerate(errors, start=1):
        file_id = str(error["file"])
        label = labels[file_id]
        prediction = predictions[file_id]
        source = images_dir / file_id
        if not source.is_file():
            raise FileNotFoundError(source)
        thumbnail_name = f"{index:02d}_{Path(file_id).stem}.jpg"
        _thumbnail(source, assets / thumbnail_name, max_side)
        reasons = " · ".join(str(item) for item in label.get("defect_reasons") or [])
        tags = " · ".join(str(item) for item in prediction.get("tags") or [])
        human_score = float(label.get("overall") or 0)
        model_score = float(prediction.get("overall_score") or 0)
        cards.append(
            f"""
    <article class="card" data-search="{html.escape((file_id + reasons + tags).casefold())}">
      <a href="{html.escape(source.resolve().as_uri())}" target="_blank">
        <img src="assets/{html.escape(thumbnail_name)}" alt="{html.escape(file_id)}" loading="lazy">
      </a>
      <section>
        <span class="index">#{index:02d}</span>
        <h2>{html.escape(file_id)}</h2>
        <div class="scores">
          <b>人工 {human_score:.1f}</b><b class="model">模型 {model_score:.1f}</b>
          <span>高估 {model_score - human_score:+.1f}</span>
        </div>
        <p><strong>人工缺陷：</strong>{html.escape(reasons)}</p>
        <p class="muted"><strong>模型标签：</strong>{html.escape(tags or "无")}</p>
      </section>
    </article>"""
        )

    document = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Stage 2 漏检 · {len(errors)} 张</title>
  <style>
    :root {{ color-scheme: dark; font-family: system-ui, sans-serif; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; color: #eee; background: #0c0d0f; }}
    header {{ position: sticky; top: 0; z-index: 2; padding: 16px 22px;
      background: #111e; backdrop-filter: blur(12px); border-bottom: 1px solid #333; }}
    h1 {{ margin: 0 0 4px; font-size: 22px; }}
    header p {{ margin: 0 0 10px; color: #aaa; }}
    input {{ width: min(480px, 100%); padding: 9px 12px; color: #eee;
      background: #202226; border: 1px solid #444; border-radius: 8px; }}
    main {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(290px, 1fr));
      gap: 16px; padding: 18px; }}
    .card {{ overflow: hidden; background: #17191d; border: 1px solid #333;
      border-radius: 10px; }}
    .card img {{ display: block; width: 100%; aspect-ratio: 3/2;
      object-fit: contain; background: #050505; }}
    section {{ padding: 11px 13px 14px; }}
    h2 {{ margin: 0 32px 10px 0; font-size: 14px; overflow-wrap: anywhere; }}
    .index {{ float: right; color: #777; }}
    .scores {{ display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }}
    .scores b, .scores span {{ padding: 4px 7px; border-radius: 6px; background: #272a30; }}
    .scores .model {{ color: #ffbc6b; }}
    p {{ margin: 10px 0 0; font-size: 13px; line-height: 1.45; }}
    .muted {{ color: #aaa; }}
    .hidden {{ display: none; }}
  </style>
</head>
<body>
  <header>
    <h1>Stage 2 语义缺陷漏检 · {len(errors)} 张</h1>
    <p>仅展示 semantic_defect + false_negative；图片已应用冻结方向校正。</p>
    <input id="search" placeholder="搜索文件名、人工缺陷或模型标签">
  </header>
  <main>{''.join(cards)}</main>
  <script>
    const cards = [...document.querySelectorAll('.card')];
    document.querySelector('#search').addEventListener('input', event => {{
      const query = event.target.value.trim().toLowerCase();
      cards.forEach(card => card.classList.toggle('hidden', !card.dataset.search.includes(query)));
    }});
  </script>
</body>
</html>
"""
    output_path.write_text(document, encoding="utf-8")
    return len(errors)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--errors", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--images", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sample-type", default="semantic_defect")
    parser.add_argument("--error-type", default="false_negative")
    args = parser.parse_args()
    count = build_gallery(
        errors_path=args.errors,
        labels_path=args.labels,
        predictions_path=args.predictions,
        images_dir=args.images,
        output_path=args.output,
        sample_type=args.sample_type,
        error_type=args.error_type,
    )
    print(f"Wrote {count} cases to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
