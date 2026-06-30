"""Per-folder gallery.html pages (static export)."""
import logging
from pathlib import Path
from typing import Any, Dict, List, Mapping

from services.processor.pipeline_image_ops import is_delivery_pipeline_mode
from services.processor.reporting.audit_io import load_audit_jsonl
from services.processor.reporting.dimension_display import score_to_percent_bar
from utils.stage3_dimensions import STAGE3_DIM_LABELS, STAGE3_DIM_ORDER

logger = logging.getLogger(__name__)

FOLDER_TITLE_MAP = {
    "best": "🌟 优质照片 (评分 ≥ 90)",
    "keep": "📸 可用照片 (评分 60-90)",
    "trash": "🗑️ 低质照片 (评分 < 60)",
}

_ORIENT_LABEL_ZH = {"landscape": "横图", "portrait": "竖图", "square": "方图"}


def _audit_score_for_sort(name: str, audit_data: Dict[str, Any]) -> float:
    row = audit_data.get(name) or {}
    try:
        raw = row.get("score")
        if raw is None:
            raw = row.get("overall_score")
        return float(raw or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _order_image_files_for_folder(
    folder_type: str,
    image_files: List[Path],
    audit_data: Dict[str, Any],
    *,
    config: Mapping[str, Any] | None,
) -> List[Path]:
    if not image_files:
        return []
    if folder_type == "best" and config is not None and is_delivery_pipeline_mode(config):
        return sorted(image_files, key=lambda p: (-_audit_score_for_sort(p.name, audit_data), p.name.lower()))
    return sorted(image_files, key=lambda p: p.name.lower())


def _orientation_from_audit(score_info: Dict[str, Any]) -> str:
    o = score_info.get("orientation")
    if o in ("landscape", "portrait", "square"):
        return o
    dbg = score_info.get("debug_info") or {}
    o = dbg.get("orientation")
    if o in ("landscape", "portrait", "square"):
        return o
    return "landscape"


def merge_layout_from_file(image_file: Path, score_info: Dict[str, Any]) -> Dict[str, Any]:
    """
    Always refresh width/height/orientation from the image file on disk.

    Audit rows can be stale (OpenCV-only size, old pipeline) or wrong; EXIF 旋转必须以
    文件为准，否则竖图会套用横图布局。
    """
    merged = dict(score_info)
    try:
        from engine.operators.image_processor import ImageProcessor

        lay = ImageProcessor.get_display_layout(str(image_file))
        if lay:
            for k in ("width", "height", "orientation"):
                if k in lay:
                    merged[k] = lay[k]
    except Exception:
        pass
    return merged


def _gallery_img_style(score_info: Dict[str, Any]) -> str:
    """Inline aspect-ratio from logical pixels (EXIF 已转正)，帮助浏览器留对区域。"""
    w = score_info.get("width")
    h = score_info.get("height")
    if (w is None or h is None) and score_info.get("debug_info"):
        dbg = score_info["debug_info"]
        w = w if w is not None else dbg.get("width")
        h = h if h is not None else dbg.get("height")
    try:
        wi, hi = int(w), int(h)
        if wi > 0 and hi > 0:
            return f' style="aspect-ratio: {wi} / {hi};"'
    except (TypeError, ValueError):
        pass
    return ""


def _orientation_meta_html(score_info: Dict[str, Any]) -> str:
    """Badge + pixel size from audit (or debug_info)."""
    o = _orientation_from_audit(score_info)
    label = _ORIENT_LABEL_ZH.get(o, "横图")
    w = score_info.get("width")
    h = score_info.get("height")
    if (w is None or h is None) and score_info.get("debug_info"):
        dbg = score_info["debug_info"]
        w = w if w is not None else dbg.get("width")
        h = h if h is not None else dbg.get("height")
    parts = [f'<span class="orient-badge">{label}</span>']
    try:
        if w is not None and h is not None:
            parts.append(f'<span class="pixel-size">{int(w)}×{int(h)}</span>')
    except (TypeError, ValueError):
        pass
    return f'<div class="image-meta">{"".join(parts)}</div>'

_GALLERY_CSS = """
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        
        .container {
            max-width: 1200px;
            margin: 0 auto;
        }
        
        .header {
            background: white;
            border-radius: 12px;
            padding: 30px;
            margin-bottom: 30px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.2);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .header-left h1 {
            color: #333;
            margin-bottom: 10px;
            font-size: 2.5em;
        }
        
        .header-left p {
            color: #666;
            font-size: 1.1em;
            margin-bottom: 20px;
        }
        
        .header-right {
            text-align: right;
            display: flex;
            flex-direction: column;
            gap: 10px;
        }
        
        .selection-counter {
            font-size: 1.1em;
            color: #333;
            font-weight: bold;
        }
        
        .export-btn {
            padding: 12px 24px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            font-size: 1em;
            font-weight: bold;
            transition: all 0.3s;
            opacity: 0.5;
            pointer-events: none;
        }
        
        .export-btn.active {
            opacity: 1;
            pointer-events: auto;
        }
        
        .export-btn:hover:not(:disabled) {
            transform: translateY(-2px);
            box-shadow: 0 5px 20px rgba(102, 126, 234, 0.4);
        }
        
        .export-btn:disabled {
            opacity: 0.5;
            cursor: not-allowed;
        }
        
        .back-link {
            display: inline-block;
            margin-bottom: 20px;
            padding: 10px 20px;
            background: #667eea;
            color: white;
            text-decoration: none;
            border-radius: 6px;
            transition: background 0.3s;
        }
        
        .back-link:hover {
            background: #764ba2;
        }
        
        .gallery {
            display: flex;
            flex-direction: column;
            gap: 20px;
        }
        
        .image-row {
            background: white;
            border-radius: 12px;
            overflow: hidden;
            box-shadow: 0 10px 30px rgba(0,0,0,0.15);
            display: flex;
            transition: all 0.3s;
        }
        
        .image-row:hover {
            box-shadow: 0 15px 40px rgba(0,0,0,0.25);
            transform: translateY(-2px);
        }
        
        .image-row.selected {
            box-shadow: 0 0 0 3px #667eea;
            background: #f8f9ff;
        }
        
        .image-col {
            background: #f0f0f0;
            display: flex;
            align-items: center;
            justify-content: center;
            overflow: hidden;
            position: relative;
            min-height: 0;
            min-width: 0;
        }
        
        /* 横图：偏宽扁区域 */
        .image-row.orientation-landscape .image-col {
            flex: 0 0 62%;
            height: 380px;
        }
        .image-row.orientation-landscape .info-col {
            flex: 0 0 38%;
        }
        /* 图片在框内按比例缩放，勿用 height:100% 以免把竖图拉成横条 */
        .image-col img.gallery-photo {
            display: block;
            margin: 0 auto;
            max-width: 100%;
            max-height: 100%;
            width: auto;
            height: auto;
            object-fit: contain;
            object-position: center;
        }
        
        /* 竖图：窄而高的区域，与竖构图比例一致 */
        .image-row.orientation-portrait .image-col {
            flex: 0 0 34%;
            min-width: 200px;
            max-width: 42%;
            min-height: 440px;
            height: min(72vh, 640px);
            max-height: 680px;
        }
        .image-row.orientation-portrait .info-col {
            flex: 1 1 58%;
            min-width: 0;
        }
        
        .image-row.orientation-square .image-col {
            flex: 0 0 50%;
            height: 420px;
        }
        .image-row.orientation-square .info-col {
            flex: 0 0 50%;
        }
        
        .image-meta {
            font-size: 0.85em;
            color: #666;
            margin-bottom: 8px;
            display: flex;
            align-items: center;
            gap: 10px;
            flex-wrap: wrap;
        }
        .orient-badge {
            font-size: 0.8em;
            padding: 3px 10px;
            border-radius: 6px;
            background: #eef0ff;
            color: #445;
        }
        
        .info-col {
            flex: 0 0 35%;
            padding: 30px;
            display: flex;
            flex-direction: column;
            justify-content: center;
            gap: 15px;
            background: white;
            overflow-y: auto;
        }
        
        .image-name {
            font-weight: bold;
            color: #333;
            word-break: break-all;
            font-size: 0.95em;
            line-height: 1.4;
        }
        
        .overall-score {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 15px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border-radius: 8px;
            font-weight: bold;
        }
        
        .score-circle {
            font-size: 1.8em;
            font-weight: bold;
        }
        
        .dimensions {
            display: flex;
            flex-direction: column;
            gap: 10px;
        }

        .dimension-note {
            font-size: 0.9em;
            color: #666;
            font-style: italic;
            padding: 4px 0;
        }
        
        .dimension {
            display: flex;
            flex-direction: column;
            gap: 5px;
        }
        
        .dimension-label {
            display: flex;
            justify-content: space-between;
            font-size: 0.9em;
            color: #666;
        }
        
        .dimension-bar {
            height: 8px;
            background: #ddd;
            border-radius: 4px;
            overflow: hidden;
        }
        
        .dimension-fill {
            height: 100%;
            background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
        }
        
        .checkbox-group {
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 15px;
            background: #f8f8f8;
            border-radius: 8px;
            cursor: pointer;
            user-select: none;
            margin-top: 10px;
        }
        
        .checkbox-group input[type="checkbox"] {
            width: 20px;
            height: 20px;
            cursor: pointer;
            accent-color: #667eea;
        }
        
        .checkbox-label {
            font-weight: 500;
            color: #333;
            cursor: pointer;
            flex: 1;
        }
        
        .empty-state {
            text-align: center;
            padding: 60px 20px;
            background: white;
            border-radius: 12px;
            color: #999;
        }
        
        .empty-state h2 {
            font-size: 1.5em;
            margin-bottom: 10px;
        }
        
        .footer {
            margin-top: 40px;
            padding: 20px;
            background: white;
            border-radius: 12px;
            text-align: center;
            color: #666;
        }
        
        @media (max-width: 900px) {
            .image-row {
                flex-direction: column;
            }
            
            .image-row.orientation-landscape .image-col,
            .image-row.orientation-portrait .image-col,
            .image-row.orientation-square .image-col {
                flex: 0 0 auto;
                width: 100%;
                min-height: 280px;
                height: auto;
                max-height: 70vh;
            }
            
            .image-row .info-col {
                flex: 0 0 auto;
                width: 100%;
            }
            
            .header {
                flex-direction: column;
                text-align: center;
            }
            
            .header-right {
                text-align: center;
                margin-top: 20px;
            }
        }
"""

_GALLERY_SCRIPT = """
    <script>
        const selectedImages = new Set();
        
        function updateSelectionCount() {
            const count = selectedImages.size;
            document.getElementById('selection-count').textContent = count;
            const exportBtn = document.getElementById('export-btn');
            if (count > 0) {
                exportBtn.classList.add('active');
                exportBtn.disabled = false;
            } else {
                exportBtn.classList.remove('active');
                exportBtn.disabled = true;
            }
        }
        
        function toggleCheckbox(element) {
            const checkbox = element.querySelector('input[type="checkbox"]');
            checkbox.checked = !checkbox.checked;
            const row = element.closest('.image-row');
            const imageName = row.dataset.image;
            if (checkbox.checked) {
                selectedImages.add(imageName);
                row.classList.add('selected');
            } else {
                selectedImages.delete(imageName);
                row.classList.remove('selected');
            }
            updateSelectionCount();
        }
        
        function exportSelected() {
            if (selectedImages.size === 0) {
                alert('请先选择要导出的图片');
                return;
            }
            const imageList = Array.from(selectedImages);
            fetch('/api/export-images', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    images: imageList,
                    category: document.title
                })
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    alert(`成功导出 ${data.count} 张图片到: ${data.export_path}`);
                    selectedImages.clear();
                    document.querySelectorAll('.image-row').forEach(row => {
                        row.classList.remove('selected');
                        row.querySelector('input[type="checkbox"]').checked = false;
                    });
                    updateSelectionCount();
                } else {
                    alert('导出失败: ' + data.error);
                }
            })
            .catch(error => {
                alert('导出出错: ' + error.message);
            });
        }
        updateSelectionCount();
    </script>
"""


def _dimension_rows_html(dimensions: Dict[str, Any], *, score_row: Mapping[str, Any] | None = None) -> str:
    if score_row and isinstance(score_row.get("stage3_result"), dict):
        sr = score_row["stage3_result"]
        if sr.get("mode") == "fast":
            dfast = sr.get("dimensions") or {}
            if all(dfast.get(k) is None for k in STAGE3_DIM_ORDER):
                return '<div class="dimension-note">Dimensions: fast estimation</div>'
    parts = []
    for key in STAGE3_DIM_ORDER:
        label = STAGE3_DIM_LABELS.get(key, key)
        raw = dimensions.get(key)
        if raw is None:
            continue
        pct = score_to_percent_bar(raw)
        disp = int(round(pct))
        parts.append(f"""
                        <div class="dimension">
                            <div class="dimension-label">
                                <span>{label}</span>
                                <span>{disp}/100</span>
                            </div>
                            <div class="dimension-bar">
                                <div class="dimension-fill" style="width: {pct}%"></div>
                            </div>
                        </div>""")
    return "\n".join(parts)


def render_folder_gallery_html(title: str, image_files: List[Path], audit_data: Dict[str, Any]) -> str:
    """Single full HTML document for one category folder."""
    n = len(image_files)
    head = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title} - Livehouse Photography</title>
    <style>
{_GALLERY_CSS}
    </style>
</head>
<body>
    <div class="container">
        <a href="preview.html" class="back-link">← 返回预览</a>
        
        <div class="header">
            <div class="header-left">
                <h1>{title}</h1>
                <p id="total-count">共 {n} 张照片</p>
            </div>
            <div class="header-right">
                <div class="selection-counter">
                    已选择: <span id="selection-count">0</span> / {n}
                </div>
                <button class="export-btn" id="export-btn" onclick="exportSelected()">
                    📥 导出选中图片
                </button>
            </div>
        </div>
"""

    if not image_files:
        body_mid = """
                <div class="empty-state">
                    <h2>没有照片</h2>
                    <p>该分类中暂无照片</p>
                </div>
"""
    else:
        rows = []
        for image_file in sorted(image_files):
            raw = audit_data.get(image_file.name, {})
            score_info = merge_layout_from_file(image_file, raw)
            overall = score_info.get("overall_score")
            if overall is None:
                overall = score_info.get("score", "N/A")
            dimensions = score_info.get("dimensions", {}) or {}
            dim_html = _dimension_rows_html(dimensions, score_row=score_info)
            orient = _orientation_from_audit(score_info)
            meta_html = _orientation_meta_html(score_info)
            img_path = image_file.name
            img_src = f"/image?path={str(image_file.absolute())}"
            img_style = _gallery_img_style(score_info)
            rows.append(
                f"""
                    <div class="image-row orientation-{orient}" data-image="{img_path}">
                        <div class="image-col">
                            <img class="gallery-photo" src="{img_src}" alt="{img_path}"{img_style} onerror="this.parentElement.innerHTML='📷 图片加载失败'">
                        </div>
                        <div class="info-col">
                            <div class="image-name" title="{img_path}">{img_path}</div>
                            {meta_html}
                            <div class="overall-score">
                                <span>总分</span>
                                <div class="score-circle">{overall}</div>
                            </div>
                            <div class="dimensions">
                                {dim_html}
                            </div>
                            <div class="checkbox-group" onclick="toggleCheckbox(this)">
                                <input type="checkbox" class="image-checkbox">
                                <label class="checkbox-label">选中此图片</label>
                            </div>
                        </div>
                    </div>"""
            )
        body_mid = '<div class="gallery">\n' + "\n".join(rows) + "\n</div>\n"

    footer = """
        <div class="footer">
            <p>图片已按照审美评分分类 | 导出后的图片将保存到本地</p>
        </div>
    </div>
""" + _GALLERY_SCRIPT + "\n</body>\n</html>"

    return head + body_mid + footer


def write_folder_gallery_pages(
    source_dir: Path,
    folders: Dict[str, Path],
    audit_file: Path,
    *,
    config: Mapping[str, Any] | None = None,
) -> list[Path]:
    """Write best/keep/trash/gallery.html using audit JSONL for scores. Returns paths written."""
    audit_data = load_audit_jsonl(audit_file)
    written: list[Path] = []

    for folder_type in ("best", "keep", "trash"):
        folder_path = folders[folder_type]
        if not folder_path.exists():
            continue

        image_files_raw = [
            f
            for f in folder_path.glob("*.*")
            if f.suffix.lower() in (".jpg", ".jpeg", ".png", ".arw")
        ]
        if not image_files_raw:
            continue

        image_files = _order_image_files_for_folder(
            folder_type, image_files_raw, audit_data, config=config
        )
        if not image_files:
            continue

        title = FOLDER_TITLE_MAP.get(folder_type, folder_type)
        html = render_folder_gallery_html(title, image_files, audit_data)
        gallery_file = folder_path / "gallery.html"
        try:
            gallery_file.write_text(html, encoding="utf-8")
            written.append(gallery_file)
            logger.info("   ✅ 已生成 %s 分类详情页: %s", folder_type, gallery_file)
        except OSError as e:
            logger.error("Failed to create gallery for %s: %s", folder_type, e)
    return written
