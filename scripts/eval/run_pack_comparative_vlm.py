#!/usr/bin/env python3
"""Build contact sheets and run checkpointed comparative pack review via DashScope."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageOps

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from inference.parsers import clean_json_response
from inference.providers.vllm import VLLMProvider
from inference.types import InferenceRequest
from scripts.eval.eval_pack_reranker import _score_pack

LABELS = tuple("ABCDEFGHIJKLMNO")
PROMPT = """你是一名资深 Livehouse 摄影选片编辑。图中是同一场演出的候选照片，每格左上角有字母。

请把整组作为一个交付 Pack 比较，而不是逐张独立打分。按以下优先级选出严格有序的 Top-5：
1. 人物主体清楚且可交付；严重遮挡、切头切脸、主体不明应显著降级。
2. 构图完整，避免明显失败的边缘截断、麦克风或前景大面积挡脸。
3. 表情、动作或现场瞬间有明确峰值。
4. 曝光、对焦和噪点足以交付。
5. 五张之间应有内容覆盖，避免近乎相同的连续帧。

不要因为灯光氛围强烈而掩盖严重构图或主体问题。只输出 JSON：
{
  "ranked_top5": ["A", "B", "C", "D", "E"],
  "must_exclude": ["F"],
  "weaker_duplicates": ["G"],
  "rationale_zh": "一句简短说明"
}
ranked_top5 必须恰好包含 5 个不重复且存在于图中的字母。"""


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _display_files(pack: dict[str, Any]) -> list[str]:
    return sorted(
        pack["files"],
        key=lambda file_id: hashlib.sha256(
            f"vlm-sheet-v1:{pack['id']}:{file_id}".encode("utf-8")
        ).hexdigest(),
    )


def build_contact_sheet(
    pack: dict[str, Any],
    output_path: Path,
    *,
    tile_width: int = 480,
    tile_height: int = 320,
    columns: int = 4,
) -> dict[str, str]:
    files = _display_files(pack)
    if len(files) > len(LABELS):
        raise ValueError(f"{pack['id']} has more than {len(LABELS)} candidates")
    rows = (len(files) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * tile_width, rows * tile_height), "#111111")
    draw = ImageDraw.Draw(sheet)
    label_map: dict[str, str] = {}
    for index, file_id in enumerate(files):
        label = LABELS[index]
        source = Path(pack["source_paths"][file_id])
        with Image.open(source) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
            image.thumbnail((tile_width, tile_height), Image.Resampling.LANCZOS)
            x = (index % columns) * tile_width
            y = (index // columns) * tile_height
            offset_x = x + (tile_width - image.width) // 2
            offset_y = y + (tile_height - image.height) // 2
            sheet.paste(image, (offset_x, offset_y))
        draw.rectangle((x + 6, y + 6, x + 45, y + 43), fill="#000000")
        draw.text((x + 18, y + 14), label, fill="#ffffff", stroke_width=1)
        label_map[label] = file_id
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output_path, format="JPEG", quality=91, optimize=True)
    return label_map


def prepare_sheets(manifest_path: Path, sheets_dir: Path) -> dict[str, Any]:
    manifest = _read_json(manifest_path)
    packs = [pack for pack in manifest.get("packs") or [] if isinstance(pack, dict)]
    sheet_rows = []
    for pack in packs:
        path = sheets_dir / f"{pack['id']}.jpg"
        label_map = build_contact_sheet(pack, path)
        sheet_rows.append(
            {
                "pack_id": pack["id"],
                "session": pack["session"],
                "split": pack["split"],
                "sheet_path": str(path),
                "label_map": label_map,
            }
        )
    sheet_manifest = {
        "schema_version": "pack_contact_sheets.v1",
        "source_manifest": str(manifest_path),
        "prompt_version": "pack_comparative_v1",
        "sheet_count": len(sheet_rows),
        "sheets": sheet_rows,
    }
    path = sheets_dir / "sheet_manifest.json"
    path.write_text(
        json.dumps(sheet_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return sheet_manifest


def validate_result(raw: Any, label_map: dict[str, str]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("response must be a JSON object")
    ranked = [str(value).strip().upper() for value in raw.get("ranked_top5") or []]
    if len(ranked) != 5 or len(set(ranked)) != 5 or not set(ranked).issubset(label_map):
        raise ValueError("ranked_top5 must contain five unique valid labels")

    def valid_optional(name: str) -> list[str]:
        labels = [str(value).strip().upper() for value in raw.get(name) or []]
        return list(dict.fromkeys(label for label in labels if label in label_map))

    return {
        "ranked_labels": ranked,
        "ranked_top5": [label_map[label] for label in ranked],
        "must_exclude": [
            label_map[label] for label in valid_optional("must_exclude")
        ],
        "weaker_duplicates": [
            label_map[label] for label in valid_optional("weaker_duplicates")
        ],
        "rationale_zh": str(raw.get("rationale_zh") or "")[:1000],
    }


def run_cloud(
    sheet_manifest: dict[str, Any],
    *,
    output_path: Path,
    endpoint: str,
    model: str,
    api_key: str,
) -> None:
    provider = VLLMProvider(
        endpoint=endpoint,
        temperature=0.0,
        num_predict=500,
        timeout=300,
        max_retries=3,
        retry_delay=1.0,
        api_key=api_key,
    )
    prior = {row["pack_id"]: row for row in _read_jsonl(output_path)}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    for sheet in sheet_manifest["sheets"]:
        pack_id = sheet["pack_id"]
        if prior.get(pack_id, {}).get("status") == "success":
            continue
        started = time.monotonic()
        response = provider.generate(
            InferenceRequest(
                image_path=sheet["sheet_path"],
                prompt=PROMPT,
                model_name=model,
                metadata={
                    "json_mode": True,
                    "vlm_thumbnail_max_side": 2048,
                    "num_predict": 500,
                },
            ),
            model_name=model,
        )
        record: dict[str, Any] = {
            "pack_id": pack_id,
            "session": sheet["session"],
            "split": sheet["split"],
            "model": response.model or model,
            "status": response.status,
            "latency_ms": round((time.monotonic() - started) * 1000),
            "usage": response.metadata,
        }
        try:
            if response.status != "success":
                raise ValueError(response.error or "cloud inference failed")
            parsed = json.loads(clean_json_response(response.text))
            record.update(validate_result(parsed, sheet["label_map"]))
        except (ValueError, json.JSONDecodeError) as exc:
            record["status"] = "error"
            record["error"] = str(exc)
            record["raw_text"] = response.text[:4000]
        with output_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        print(f"{pack_id}: {record['status']}")


def evaluate_cloud(
    sheet_manifest: dict[str, Any],
    *,
    results_path: Path,
    reviews_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    reviews = {row["pack_id"]: row for row in _read_jsonl(reviews_path)}
    latest = {row["pack_id"]: row for row in _read_jsonl(results_path)}
    cases = []
    for sheet in sheet_manifest["sheets"]:
        result = latest.get(sheet["pack_id"])
        if not result or result.get("status") != "success":
            continue
        cases.append(
            {
                "pack_id": sheet["pack_id"],
                "session": sheet["session"],
                "split": sheet["split"],
                **_score_pack(
                    result["ranked_top5"],
                    reviews[sheet["pack_id"]]["selected_ids"],
                ),
            }
        )
    arms: dict[str, Any] = {}
    for split in ("development", "holdout"):
        rows = [case for case in cases if case["split"] == split]
        arms[split] = {
            "pack_count": len(rows),
            "macro_overlap_at_5": (
                sum(row["overlap_at_5"] for row in rows) / len(rows) if rows else None
            ),
            "macro_ndcg_at_5": (
                sum(row["ndcg_at_5"] for row in rows) / len(rows) if rows else None
            ),
            "top1_accuracy": (
                sum(row["top1_match"] for row in rows) / len(rows) if rows else None
            ),
            "cases": rows,
        }
    report = {
        "schema_version": "pack_comparative_vlm_eval.v1",
        "prompt_version": sheet_manifest["prompt_version"],
        **arms,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packs", type=Path, required=True)
    parser.add_argument("--sheets-dir", type=Path, required=True)
    parser.add_argument("--results", type=Path)
    parser.add_argument("--reviews", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument(
        "--endpoint",
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    parser.add_argument("--model", default="qwen3-vl-plus")
    args = parser.parse_args()
    sheet_manifest = prepare_sheets(args.packs, args.sheets_dir)
    print(f"prepared {sheet_manifest['sheet_count']} contact sheets")
    if args.prepare_only:
        return 0
    if args.results is None or args.reviews is None or args.report is None:
        raise SystemExit("--results, --reviews and --report are required for cloud mode")
    api_key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("DASHSCOPE_API_KEY is missing")
    run_cloud(
        sheet_manifest,
        output_path=args.results,
        endpoint=args.endpoint,
        model=args.model,
        api_key=api_key,
    )
    report = evaluate_cloud(
        sheet_manifest,
        results_path=args.results,
        reviews_path=args.reviews,
        output_path=args.report,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
