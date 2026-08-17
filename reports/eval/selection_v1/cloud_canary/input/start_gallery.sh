#!/bin/bash
# Auto-generated: start gallery_server.py from project root copy

cd "/Volumes/M4Buffer/workspace/Livehouse-Photography-Agent/reports/eval/selection_v1/cloud_canary/input"

gallery_server_path="gallery_server.py"
if [ ! -f "$gallery_server_path" ]; then
    cp "/Volumes/M4Buffer/workspace/Livehouse-Photography-Agent/gallery_server.py" .
fi

echo "🚀 启动双排流Gallery服务器..."
echo "🌍 请访问: http://localhost:8080"
python gallery_server.py
