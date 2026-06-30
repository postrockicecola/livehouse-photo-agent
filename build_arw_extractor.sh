#!/bin/bash

# ARW 提取工具编译脚本

set -e

echo "🔨 编译 ARW 提取工具..."

# 检查 Go 环境
if ! command -v go &> /dev/null; then
    echo "❌ Go 环境未找到，请先安装 Go"
    exit 1
fi

# 编译
GOOS=darwin GOARCH=arm64 go build -o arw_extractor arw_extractor.go

if [ $? -eq 0 ]; then
    echo "✅ 编译成功！"
    echo ""
    echo "使用方法:"
    echo "  ./arw_extractor -input /path/to/arw/folder"
    echo ""
    echo "更多选项:"
    echo "  ./arw_extractor -help"
else
    echo "❌ 编译失败"
    exit 1
fi
