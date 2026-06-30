"""Static preview.html generation for post-pipeline summary."""
from pathlib import Path
from typing import Dict, Mapping


def build_preview_html(
    stats: Dict[str, int],
    best_count: int,
    keep_count: int,
    trash_count: int,
) -> str:
    """Build preview.html body (full document)."""
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Livehouse Photography - AI Selection Preview</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
        }}
        .container {{
            background: white;
            border-radius: 12px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            padding: 40px;
        }}
        h1 {{
            text-align: center;
            color: #333;
            margin-bottom: 10px;
            font-size: 2.5em;
        }}
        .subtitle {{
            text-align: center;
            color: #666;
            margin-bottom: 40px;
            font-size: 1.1em;
        }}
        .stats {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin-bottom: 40px;
        }}
        .stat-box {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 20px;
            border-radius: 8px;
            text-align: center;
        }}
        .stat-number {{
            font-size: 2.5em;
            font-weight: bold;
            margin-bottom: 10px;
        }}
        .stat-label {{
            font-size: 1em;
            opacity: 0.9;
        }}
        .results {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
            margin-bottom: 40px;
        }}
        .result-card {{
            border: 2px solid #ddd;
            border-radius: 8px;
            padding: 20px;
            text-align: center;
            transition: all 0.3s ease;
        }}
        .result-card:hover {{
            border-color: #667eea;
            box-shadow: 0 5px 20px rgba(102, 126, 234, 0.2);
        }}
        .result-card.best {{
            border-color: #4CAF50;
            background: rgba(76, 175, 80, 0.05);
        }}
        .result-card.keep {{
            border-color: #2196F3;
            background: rgba(33, 150, 243, 0.05);
        }}
        .result-card.trash {{
            border-color: #f44336;
            background: rgba(244, 67, 54, 0.05);
        }}
        .result-card h3 {{
            margin: 0 0 10px 0;
        }}
        .result-card.best h3 {{ color: #4CAF50; }}
        .result-card.keep h3 {{ color: #2196F3; }}
        .result-card.trash h3 {{ color: #f44336; }}
        .result-count {{
            font-size: 2em;
            font-weight: bold;
            margin: 15px 0;
        }}
        .result-card.best .result-count {{ color: #4CAF50; }}
        .result-card.keep .result-count {{ color: #2196F3; }}
        .result-card.trash .result-count {{ color: #f44336; }}
        .folder-link {{
            display: inline-block;
            margin-top: 10px;
            padding: 8px 16px;
            background: #667eea;
            color: white;
            text-decoration: none;
            border-radius: 4px;
            font-size: 0.9em;
            transition: background 0.3s;
        }}
        .folder-link:hover {{
            background: #764ba2;
        }}
        .info {{
            background: #f5f5f5;
            padding: 20px;
            border-radius: 8px;
            margin-top: 30px;
            border-left: 4px solid #667eea;
        }}
        .info h3 {{
            margin-top: 0;
            color: #667eea;
        }}
        .info p {{
            margin: 10px 0;
            color: #666;
            line-height: 1.6;
        }}
        code {{
            background: #f0f0f0;
            padding: 2px 6px;
            border-radius: 3px;
            font-family: 'Courier New', monospace;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>🎬 Livehouse 摄影 AI 选片系统</h1>
        <p class="subtitle">✨ 处理完成 - 结果预览</p>
        
        <div class="stats">
            <div class="stat-box">
                <div class="stat-number">{stats['processed']}</div>
                <div class="stat-label">已处理</div>
            </div>
            <div class="stat-box">
                <div class="stat-number">{stats['failed']}</div>
                <div class="stat-label">处理失败</div>
            </div>
            <div class="stat-box">
                <div class="stat-number">{stats['skipped']}</div>
                <div class="stat-label">跳过（已存在）</div>
            </div>
            <div class="stat-box">
                <div class="stat-number">{stats['fast_rejected']}</div>
                <div class="stat-label">快速拒绝</div>
            </div>
        </div>
        
        <div class="results">
            <div class="result-card best">
                <h3>🌟 Best</h3>
                <p>评分 ≥ 90</p>
                <div class="result-count">{best_count}</div>
                <p>最优质照片</p>
                <a href="best/gallery.html" class="folder-link">查看详情</a>
            </div>
            
            <div class="result-card keep">
                <h3>📸 Keep</h3>
                <p>评分 60-90</p>
                <div class="result-count">{keep_count}</div>
                <p>可用照片</p>
                <a href="keep/gallery.html" class="folder-link">查看详情</a>
            </div>
            
            <div class="result-card trash">
                <h3>🗑️ Trash</h3>
                <p>评分 < 60</p>
                <div class="result-count">{trash_count}</div>
                <p>低质照片</p>
                <a href="trash/gallery.html" class="folder-link">查看详情</a>
            </div>
        </div>
        
        <div class="info">
            <h3>📊 更多功能</h3>
            <p>
                <strong>查看详细评分记录：</strong><br>
                <code>aesthetic_audit.jsonl</code> - 包含每张照片的详细评分和分析<br>
                <code>pipeline.log</code> - 处理过程日志
            </p>
            <p>
                <strong>启动 Web 服务器查看完整功能：</strong><br>
                在相同目录运行 <code>python gallery_server.py</code>
            </p>
            <p style="font-size: 0.9em; color: #999; margin-top: 20px;">
                此页面由 Livehouse AI Selection System 自动生成
            </p>
        </div>
    </div>
</body>
</html>"""


def write_preview_html_with_folders(
    source_dir: Path,
    stats: Dict[str, int],
    folders: Mapping[str, Path],
) -> Path:
    """Write preview.html; returns path written."""
    best_count = len(list(folders["best"].glob("*.*"))) if folders["best"].exists() else 0
    keep_count = len(list(folders["keep"].glob("*.*"))) if folders["keep"].exists() else 0
    trash_count = len(list(folders["trash"].glob("*.*"))) if folders["trash"].exists() else 0

    preview_file = source_dir / "preview.html"
    html = build_preview_html(stats, best_count, keep_count, trash_count)
    preview_file.write_text(html, encoding="utf-8")
    return preview_file
