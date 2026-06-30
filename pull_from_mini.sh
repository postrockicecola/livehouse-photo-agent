#!/bin/bash
# 从远程主机同步代码到本地
# --update 表示只同步比本地更新的文件，避免覆盖你刚写的代码
#
# 用环境变量配置远程地址，避免把私有主机/用户名写进仓库：
#   REMOTE_USER  远程用户名（默认 user）
#   REMOTE_HOST  远程主机名或 IP（必填）
#   REMOTE_PATH  远程仓库路径（默认 ~/Livehouse-Photography-Agent/）
# 例：REMOTE_HOST=192.0.2.10 ./pull_from_mini.sh
set -euo pipefail

REMOTE_USER="${REMOTE_USER:-user}"
REMOTE_PATH="${REMOTE_PATH:-~/Livehouse-Photography-Agent/}"

if [[ -z "${REMOTE_HOST:-}" ]]; then
    echo "请先设置 REMOTE_HOST，例如：REMOTE_HOST=your-host ./pull_from_mini.sh" >&2
    exit 1
fi

rsync -avz --update \
    --exclude '.git/' \
    --exclude '__pycache__/' \
    "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_PATH}" ./
