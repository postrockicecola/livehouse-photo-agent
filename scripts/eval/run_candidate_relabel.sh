#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ROUND_DIR="${1:-data/eval/candidate_rounds/round_001}"
MODEL="${QWEN_VL_MODEL:-qwen3-vl-plus}"

: "${DASHSCOPE_API_KEY:?Export DASHSCOPE_API_KEY before running this script}"

cd "$ROOT"

EXTRA_ARGS=()
if [[ "${QWEN_SEMANTIC_TAXONOMY:-0}" == "1" ]]; then
  EXTRA_ARGS+=(--semantic-taxonomy)
fi

python scripts/eval/prepare_candidate_relabel.py \
  "$ROUND_DIR/candidates.jsonl"

python scripts/eval/relabel_qwen.py score \
  --images "$ROUND_DIR/relabel_images" \
  --manifest "$ROUND_DIR/relabel_manifest.json" \
  --out "$ROUND_DIR/qwen_suggestions.jsonl" \
  --model "$MODEL" \
  --scoring-mode split2 \
  --max-edge 1280 \
  --jpeg-quality 85 \
  --temperature 0 \
  --concurrency 4 \
  --blind-fraction 0.15 \
  "${EXTRA_ARGS[@]}"
