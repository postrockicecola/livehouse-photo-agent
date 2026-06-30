"""analysis_results.json and optional gallery_server launch scripts."""
import json
import logging
from pathlib import Path
from typing import Any, Dict, List

from utils.repo_paths import repo_root

from engine.operators.image_processor import ImageProcessor
from services.processor.reporting.audit_io import load_audit_jsonl
from utils.stage3_dimensions import STAGE3_DIM_KEYS

logger = logging.getLogger(__name__)


def _mean_dims(dimensions: Dict[str, Any], keys: tuple[str, ...]) -> float:
    vals: list[float] = []
    for k in keys:
        raw = dimensions.get(k)
        if raw is None:
            continue
        try:
            vals.append(float(raw))
        except (TypeError, ValueError):
            continue
    if not vals:
        return 0.0
    return round(sum(vals) / len(vals), 2)


def _dim_to_gallery_float(raw: Any) -> float:
    """Model uses 0–10; normalize to ~0–10 for JSON consumers."""
    if raw is None:
        return 0.0
    try:
        x = float(raw)
    except (TypeError, ValueError):
        return 0.0
    if x > 10.0:
        return round(x / 10.0, 2)
    return round(x, 2)


def build_analysis_results(
    source_dir: Path,
    folders: Dict[str, Path],
    audit_file: Path,
) -> List[Dict[str, Any]]:
    """Build list of records for gallery_server analysis_results.json."""
    audit_data = load_audit_jsonl(audit_file)
    results: List[Dict[str, Any]] = []

    for category in ("best", "keep", "trash"):
        folder_path = folders[category]
        if not folder_path.exists():
            continue

        for image_file in folder_path.glob("*.*"):
            if image_file.suffix.lower() not in (".jpg", ".jpeg", ".png"):
                continue

            audit_entry = audit_data.get(image_file.name, {})
            dimensions = audit_entry.get("dimensions", {}) or {}
            raw_overall = audit_entry.get("overall_score")
            if raw_overall is None:
                raw_overall = audit_entry.get("score")
            try:
                overall_score = round(float(raw_overall), 2) if raw_overall is not None else 0.0
            except (TypeError, ValueError):
                overall_score = 0.0
            overall_score = max(0.0, min(100.0, overall_score))

            energy = _mean_dims(
                dimensions,
                ("moment_peak", "atmosphere_impact"),
            )
            technical = _mean_dims(
                dimensions,
                ("focus_sharpness", "exposure_control", "noise_cleanliness"),
            )
            composition = _mean_dims(
                dimensions,
                ("composition_framing", "light_color_character"),
            )
            # Backward compat: old audit rows only have legacy four keys
            if not any(k in dimensions for k in STAGE3_DIM_KEYS):
                energy = _dim_to_gallery_float(dimensions.get("atmosphere", 0))
                technical = _dim_to_gallery_float(dimensions.get("subject_clarity", 0))
                composition = _dim_to_gallery_float(dimensions.get("lighting_quality", 0))

            dbg = audit_entry.get("debug_info") or {}
            lap = dbg.get("laplacian_var") or dbg.get("laplacian") or 0
            try:
                laplacian = round(float(lap), 2) if lap else 0
            except (TypeError, ValueError):
                laplacian = 0

            ow = audit_entry.get("width", dbg.get("width"))
            oh = audit_entry.get("height", dbg.get("height"))
            orient = audit_entry.get("orientation", dbg.get("orientation"))

            row = {
                "file": image_file.name,
                "path": str(image_file),
                "category": category,
                "overall_score": overall_score,
                "energy": energy,
                "technical": technical,
                "composition": composition,
                "scores": {
                    "overall": overall_score,
                    "energy": energy,
                    "technical": technical,
                    "composition": composition,
                    "laplacian": laplacian,
                },
            }
            try:
                ph = int(dbg.get("phash") or 0)
                if ph:
                    row["phash"] = ph
            except (TypeError, ValueError):
                pass
            if dimensions and isinstance(dimensions, dict):
                dim_snap = {}
                for dk in STAGE3_DIM_KEYS:
                    if dk in dimensions:
                        try:
                            dim_snap[dk] = round(float(dimensions[dk]), 2)
                        except (TypeError, ValueError):
                            pass
                if dim_snap:
                    row["dimensions"] = dim_snap
            if orient:
                row["orientation"] = orient
            if ow is not None:
                row["width"] = int(ow) if isinstance(ow, (int, float)) else ow
            if oh is not None:
                row["height"] = int(oh) if isinstance(oh, (int, float)) else oh

            # 始终以磁盘文件为准（含 EXIF），避免审计旧数据导致双排流首页横竖错误
            layout = ImageProcessor.get_display_layout(str(image_file))
            if layout:
                for k in ("width", "height", "orientation"):
                    if k in layout:
                        row[k] = layout[k]

            # 与 ``load_gallery_page`` / ``inject_orientation`` 一致：JPEG EXIF 为 1 但 RAW 仍带旋转时，
            # 写入 ``rotate_degrees`` 并同步展示宽高，避免「竖拍预览被压成横图尺寸」的 JSON。
            try:
                from services.path_service import PathResolver
                from services.result_service import inject_orientation

                inj_row = dict(row)
                inj_row["path"] = str(image_file.resolve())
                inj_row["file"] = image_file.name
                inject_orientation(inj_row, PathResolver(source_dir.resolve()))
                for k in ("width", "height", "orientation"):
                    if k in inj_row:
                        row[k] = inj_row[k]
                if "rotate_degrees" in inj_row:
                    row["rotate_degrees"] = inj_row["rotate_degrees"]
                else:
                    row.pop("rotate_degrees", None)
            except Exception:
                logger.debug("gallery JSON orientation align failed for %s", image_file, exc_info=True)

            dc = audit_entry.get("dimension_comments")
            if dc:
                row["dimension_comments"] = dc
            es = audit_entry.get("editing_suggestions")
            if es:
                row["editing_suggestions"] = es
            rb = audit_entry.get("reason_bilingual")
            if rb:
                row["reason_bilingual"] = rb
            wb = audit_entry.get("weakness_bilingual")
            if wb:
                row["weakness_bilingual"] = wb
            if audit_entry.get("reason"):
                row["reason"] = audit_entry["reason"]
            if audit_entry.get("weakness"):
                row["weakness"] = audit_entry["weakness"]
            tags = audit_entry.get("tags")
            if isinstance(tags, list) and tags:
                row["tags"] = tags

            results.append(row)

    return results


def write_analysis_results_json(source_dir: Path, folders: Dict[str, Path], audit_file: Path) -> Path:
    """Write analysis_results.json next to source images."""
    results = build_analysis_results(source_dir, folders, audit_file)
    output_path = source_dir / "analysis_results.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    logger.info("   [gallery] analysis_results.json → %s （%s 条）", output_path, len(results))
    return output_path


def write_gallery_launch_scripts(source_dir: Path) -> list[Path]:
    """Write start_gallery.sh / .bat that copy gallery_server.py from repo if missing. Returns paths written."""
    root = repo_root()
    gallery_server = root / "gallery_server.py"

    script_content = f"""#!/bin/bash
# Auto-generated: start gallery_server.py from project root copy

cd "{source_dir}"

gallery_server_path="gallery_server.py"
if [ ! -f "$gallery_server_path" ]; then
    cp "{gallery_server}" .
fi

echo "🚀 启动双排流Gallery服务器..."
echo "🌍 请访问: http://localhost:8080"
python gallery_server.py
"""

    written: list[Path] = []
    script_path = source_dir / "start_gallery.sh"
    script_path.write_text(script_content, encoding="utf-8")
    script_path.chmod(0o755)
    written.append(script_path)

    bat_content = f"""@echo off
cd /D "{source_dir}"

if not exist "gallery_server.py" (
    copy "{gallery_server}" .
)

echo 🚀 启动双排流Gallery服务器...
echo 🌍 请访问: http://localhost:8080
python gallery_server.py
"""

    bat_path = source_dir / "start_gallery.bat"
    bat_path.write_text(bat_content, encoding="utf-8")
    written.append(bat_path)

    logger.info("   [gallery] 备用启动脚本: %s | %s", script_path, bat_path)
    return written
