#!/bin/bash

# Livehouse 照片处理完整工作流脚本

set -e

echo "╔════════════════════════════════════════════════════════════╗"
echo "║        Livehouse 照片处理完整工作流                         ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""

# 配置
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ARW_EXTRACTOR="$PROJECT_DIR/arw_extractor"
TEST_SCRIPT="$PROJECT_DIR/tests/test_ai.py"
SERVE_SCRIPT="$PROJECT_DIR/../Previews/serve.py"

# 检查参数
if [ $# -lt 1 ]; then
    echo "❌ 用法: $0 <ARW文件夹路径>"
    echo ""
    echo "示例:"
    echo "  $0 /path/to/Livehouse_Archive/session"
    echo ""
    exit 1
fi

ARW_INPUT="$1"

# 验证输入目录
if [ ! -d "$ARW_INPUT" ]; then
    echo "❌ 错误: 目录不存在: $ARW_INPUT"
    exit 1
fi

echo "🎬 开始处理流程..."
echo ""

# 步骤 1: 提取 JPEG
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📷 步骤 1: 从 ARW 文件提取 JPEG 预览"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

if [ ! -f "$ARW_EXTRACTOR" ]; then
    echo "⚠️  编译工具中..."
    cd "$PROJECT_DIR"
    go build -o arw_extractor arw_extractor.go
    cd - > /dev/null
fi

REVIEWS_DIR="$ARW_INPUT/reviews"
"$ARW_EXTRACTOR" -input "$ARW_INPUT" -verify

echo ""
echo "✅ JPEG 提取完成"
echo "   输出目录: $REVIEWS_DIR"
echo ""

# 步骤 2: 运行 AI 评分
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🤖 步骤 2: 使用 AI 进行照片评分和分类"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# 更新 SOURCE_DIR 到 reviews 目录
export LIVEHOUSE_INPUT="$REVIEWS_DIR"
cd "$PROJECT_DIR"
python "$TEST_SCRIPT"
cd - > /dev/null

echo ""
echo "✅ AI 评分完成"
echo ""

# 步骤 3: 启动预览服务器
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🌐 步骤 3: 启动预览服务器"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

echo "🌐 启动服务器..."
cd "$REVIEWS_DIR"
python "$SERVE_SCRIPT" &
SERVER_PID=$!

sleep 2

echo ""
echo "╔════════════════════════════════════════════════════════════╗"
echo "║                    ✅ 所有步骤完成！                        ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""
echo "📊 处理结果:"
echo "  1. 📷 ARW 文件提取: $REVIEWS_DIR"
echo "  2. 🤖 AI 评分分类: 已完成"
echo "  3. 🌐 预览网页: http://localhost:8000/preview.html"
echo ""
echo "💡 后续操作:"
echo "  • 打开浏览器访问预览网页"
echo "  • 选择你想要的照片"
echo "  • 导入到 manual_selected 文件夹"
echo ""
echo "📁 文件夹结构:"
echo "  $REVIEWS_DIR/"
echo "  ├── *.jpg (原始JPEG文件)"
echo "  ├── AI_Best_90+/ (评分 ≥ 90)"
echo "  ├── AI_Keep_60-90/ (评分 60-90)"
echo "  ├── AI_Trash_Below60/ (评分 < 60)"
echo "  ├── AI_Selected_Final/ (自动精选)"
echo "  ├── manual_selected/ (手动选择)"
echo "  ├── preview.html (预览网页)"
echo "  └── aesthetic_audit.jsonl (评分日志)"
echo ""
echo "⏹️  按 Ctrl+C 停止服务器"
echo ""

# 等待服务器
wait $SERVER_PID
