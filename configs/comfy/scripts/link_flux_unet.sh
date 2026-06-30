#!/usr/bin/env bash
# 若 flux1-dev.safetensors 下载在 checkpoints/，链到 diffusion_models/ 供 UNETLoader 识别
set -euo pipefail
COMFY="${COMFY_ROOT:-$HOME/ComfyUI}"
SRC="$COMFY/models/checkpoints/flux1-dev.safetensors"
DST="$COMFY/models/diffusion_models/flux1-dev.safetensors"
mkdir -p "$COMFY/models/diffusion_models"
if [[ ! -f "$SRC" ]]; then
  echo "缺少: $SRC"
  exit 1
fi
ln -sf ../checkpoints/flux1-dev.safetensors "$DST"
echo "OK: $DST ->"
ls -lh "$DST"
