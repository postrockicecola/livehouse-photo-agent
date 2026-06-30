#!/usr/bin/env bash
# 在 ComfyUI 安装 cubiq/ComfyUI_InstantID（维护模式但仍常用）+ Python 依赖
set -euo pipefail
COMFY="${COMFY_ROOT:-$HOME/ComfyUI}"
NODES="$COMFY/custom_nodes/ComfyUI_InstantID"

if [[ -d "$NODES/.git" ]]; then
  echo "已存在: $NODES"
else
  git clone https://github.com/cubiq/ComfyUI_InstantID.git "$NODES"
fi

if [[ -f "$COMFY/.venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "$COMFY/.venv/bin/activate"
elif [[ -f "$COMFY/venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "$COMFY/venv/bin/activate"
fi

pip install -U insightface onnxruntime

echo "完成。请重启 ComfyUI，并按 configs/comfy/GUIDE_INSTANTID_STEP_BY_STEP.md 下载模型。"
