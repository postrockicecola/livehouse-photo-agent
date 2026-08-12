#!/usr/bin/env python3
"""Run the production local Stage3 fast prompt over an orientation-normalized dev set."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.operators.image_processor import ImageProcessor  # noqa: E402
from inference.parsers import clean_json_response, parse_fast_vlm_response  # noqa: E402
from services.processor.stages.semantic_gate import evaluate_semantic_gate  # noqa: E402
from services.processor.stages.stage3_output_validation import (  # noqa: E402
    sanitize_stage3_parsed,
)
from services.processor.stages.stage3_prompt_builder import (  # noqa: E402
    STAGE3_PROMPT_VERSION,
    build_stage3_fast_prompt,
    build_stage3_semantic_compact_prompt,
    build_stage3_semantic_first_prompt,
)
from utils.config_loader import ConfigLoader  # noqa: E402


def _existing(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    return {
        str(row["file"])
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
        for row in [json.loads(line)]
        if row.get("file")
    }


def run(
    config_path: Path,
    images_dir: Path,
    output: Path,
    *,
    prompt_variant: str = "current",
    file_list: Path | None = None,
    model_name_override: str | None = None,
    num_predict_override: int | None = None,
) -> dict[str, Any]:
    config = ConfigLoader.load(str(config_path))
    model = config.get("model") or {}
    stage3 = config.get("stage3") or {}
    endpoint = str(model.get("endpoint") or "http://127.0.0.1:11434").rstrip("/")
    model_name = model_name_override or str(
        model.get("model_name") or "qwen2.5vl:7b"
    )
    timeout = float(model.get("timeout") or 300)
    num_predict = num_predict_override or int(
        stage3.get("fast_num_predict") or 220
    )
    if prompt_variant == "semantic_compact":
        prompt = build_stage3_semantic_compact_prompt()
    elif prompt_variant == "semantic_first":
        prompt = build_stage3_semantic_first_prompt()
    else:
        prompt = build_stage3_fast_prompt(blur_eff=None, stage1_features=None)
    allowed = (
        {
            line.strip()
            for line in file_list.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
        if file_list
        else None
    )
    files = sorted(
        path
        for path in images_dir.iterdir()
        if path.suffix.lower() in {".jpg", ".jpeg", ".png"}
        and (allowed is None or path.name in allowed)
    )
    if allowed is not None and {path.name for path in files} != allowed:
        raise ValueError("file list differs from available images")
    done = _existing(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    failures = 0
    with output.open("a", encoding="utf-8") as handle:
        for index, image_path in enumerate(files, start=1):
            if image_path.name in done:
                continue
            encoded = ImageProcessor.get_optimized_base64(str(image_path))
            started = time.perf_counter()
            parsed: dict[str, Any] = {}
            raw = ""
            error = ""
            for attempt, token_cap in enumerate(
                (num_predict, max(num_predict * 2, 320)),
                start=1,
            ):
                try:
                    response = requests.post(
                        f"{endpoint}/api/generate",
                        json={
                            "model": model_name,
                            "prompt": prompt,
                            "images": [encoded],
                            "stream": False,
                            "think": False,
                            "format": "json",
                            "options": {"temperature": 0, "num_predict": token_cap},
                        },
                        timeout=timeout,
                    )
                    response.raise_for_status()
                    payload = response.json()
                    raw = str(
                        payload.get("response")
                        or payload.get("thinking")
                        or ""
                    )
                    parsed = parse_fast_vlm_response(clean_json_response(raw), raw)
                    if parsed:
                        break
                    error = "parse_failure"
                except Exception as exc:  # noqa: BLE001
                    error = f"{type(exc).__name__}: {exc}"
                if attempt == 1:
                    continue
            parsed = sanitize_stage3_parsed(parsed) if parsed else {}
            gate = evaluate_semantic_gate(parsed.get("semantic_gate"), config)
            row = {
                "schema_version": "semantic_gate_observe_prediction.v1",
                "file": image_path.name,
                "score": parsed.get("score"),
                "semantic_gate": gate,
                "tags": parsed.get("tags") or [],
                "model": model_name,
                "prompt_version": STAGE3_PROMPT_VERSION,
                "prompt_variant": prompt_variant,
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                "error": error if not parsed else None,
            }
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
            failures += int(not parsed)
            print(
                f"[{index}/{len(files)}] {image_path.name} "
                f"gate={gate['status']} score={parsed.get('score')} "
                f"{row['latency_ms']:.0f}ms",
                flush=True,
            )
    return {"count": len(files), "already_done": len(done), "failures": failures}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "configs/livehouse.yaml")
    parser.add_argument("--images", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--prompt-variant",
        choices=("current", "semantic_first", "semantic_compact"),
        default="current",
    )
    parser.add_argument("--file-list", type=Path)
    parser.add_argument("--model-name")
    parser.add_argument("--num-predict", type=int)
    args = parser.parse_args()
    print(
        json.dumps(
            run(
                args.config,
                args.images,
                args.output,
                prompt_variant=args.prompt_variant,
                file_list=args.file_list,
                model_name_override=args.model_name,
                num_predict_override=args.num_predict,
            ),
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
