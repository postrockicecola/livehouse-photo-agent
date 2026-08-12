#!/usr/bin/env python3
"""Benchmark an MLX-VLM semantic gate on an orientation-normalized dev set."""
from __future__ import annotations

import argparse
import json
import resource
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import mlx.core as mx
from mlx_vlm import generate, load
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from inference.parsers import clean_json_response, parse_fast_vlm_response  # noqa: E402
from services.processor.stages.semantic_gate import evaluate_semantic_gate  # noqa: E402
from services.processor.stages.stage3_output_validation import (  # noqa: E402
    sanitize_stage3_parsed,
)
from services.processor.stages.stage3_prompt_builder import (  # noqa: E402
    build_stage3_semantic_compact_prompt,
)
from utils.config_loader import ConfigLoader  # noqa: E402


def _completed(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    return {
        str(row["file"])
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
        for row in [json.loads(line)]
        if row.get("file")
    }


def _resize(source: Path, destination: Path, max_dimension: int) -> None:
    with Image.open(source) as image:
        image = image.convert("RGB")
        image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
        image.save(destination, format="JPEG", quality=90)


def run(args: argparse.Namespace) -> dict[str, Any]:
    config = ConfigLoader.load(str(args.config))
    allowed = {
        line.strip()
        for line in args.file_list.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    files = sorted(
        path
        for path in args.images.iterdir()
        if path.name in allowed
    )
    if {path.name for path in files} != allowed:
        raise ValueError("file list differs from available images")
    done = _completed(args.output)
    args.output.parent.mkdir(parents=True, exist_ok=True)

    load_started = time.perf_counter()
    model, processor = load(args.model)
    load_ms = round((time.perf_counter() - load_started) * 1000, 1)
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image"},
                {"type": "text", "text": build_stage3_semantic_compact_prompt()},
            ],
        }
    ]
    prompt = processor.apply_chat_template(
        messages,
        tokenize=False,
        add_generation_prompt=True,
        enable_thinking=False,
    )
    failures = 0
    with tempfile.TemporaryDirectory(prefix="semantic-mlx-") as temp_dir:
        temp_root = Path(temp_dir)
        with args.output.open("a", encoding="utf-8") as handle:
            for index, source in enumerate(files, start=1):
                if source.name in done:
                    continue
                prepared = temp_root / f"{index:03d}.jpg"
                _resize(source, prepared, args.max_image_dimension)
                started = time.perf_counter()
                error = ""
                parsed: dict[str, Any] = {}
                raw = ""
                try:
                    result = generate(
                        model,
                        processor,
                        prompt,
                        image=str(prepared),
                        max_tokens=args.max_tokens,
                        temperature=0.0,
                        verbose=False,
                    )
                    raw = result.text
                    parsed = parse_fast_vlm_response(
                        clean_json_response(raw),
                        raw,
                    )
                except Exception as exc:  # noqa: BLE001
                    error = f"{type(exc).__name__}: {exc}"
                parsed = sanitize_stage3_parsed(parsed) if parsed else {}
                gate = evaluate_semantic_gate(parsed.get("semantic_gate"), config)
                row = {
                    "schema_version": "semantic_gate_mlx_prediction.v1",
                    "file": source.name,
                    "score": parsed.get("score"),
                    "semantic_gate": gate,
                    "tags": parsed.get("tags") or [],
                    "model": args.model,
                    "prompt_variant": "semantic_compact",
                    "load_ms": load_ms,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                    "peak_rss_mb": round(
                        resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024**2,
                        1,
                    ),
                    "error": error if not parsed else None,
                }
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                handle.flush()
                failures += int(not parsed)
                print(
                    f"[{index}/{len(files)}] {source.name} "
                    f"gate={gate['status']} score={parsed.get('score')} "
                    f"{row['latency_ms']:.0f}ms rss={row['peak_rss_mb']}MB",
                    flush=True,
                )
                mx.clear_cache()
    return {
        "count": len(files),
        "already_done": len(done),
        "failures": failures,
        "load_ms": load_ms,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "configs/livehouse.yaml")
    parser.add_argument("--images", type=Path, required=True)
    parser.add_argument("--file-list", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--model",
        default="mlx-community/Qwen3-VL-30B-A3B-Instruct-3bit",
    )
    parser.add_argument("--max-image-dimension", type=int, default=768)
    parser.add_argument("--max-tokens", type=int, default=512)
    args = parser.parse_args()
    print(json.dumps(run(args), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
