@echo off
cd /D "/Volumes/M4Buffer/workspace/Livehouse-Photography-Agent/data/eval/score_calibration_v1/images"

if not exist "gallery_server.py" (
    copy "/Volumes/M4Buffer/workspace/Livehouse-Photography-Agent/gallery_server.py" .
)

echo 🚀 启动双排流Gallery服务器...
echo 🌍 请访问: http://localhost:8080
python gallery_server.py
