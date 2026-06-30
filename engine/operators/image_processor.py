"""Image processing and quality assessment for Livehouse photography pipelines.

设计说明（Livehouse bias）
------------------------
舞台场景常见：射灯高光、烟雾低对比、有意动态模糊。旧版 Stage1 对过曝/欠曝/低对比
直接 ``return False``，会把大量可交给 VLM 的片子挡在 Stage3 之外。

本版策略：
- **硬拒绝（passes_quality=False）** 仅用于：读图失败、无结构的全糊、接近纯色无信息、
  全黑/全白废片。其余情况一律 **放行**，用 ``tech_score`` 与 ``debug_info`` 记录惩罚。
- **动感模糊** 仅在梯度方向性异常 **且** 边缘密度低于地板时才标为 motion blur，避免
  单向舞台光或线条被误判。
- **曝光** 用亮度分位数与自适应高光/阴影占比，替代固定 230/30 直方图 bin。
- **构图** 用「高亮区域质心 + 与中心/三分点的距离」简化近似主体位置（无专用检测器时的工程折中）。

性能：``assess_image_quality`` / ``fast_aesthetic_assessment`` / ``assess_composition`` 支持
可选传入已解码的 ``img_bgr`` / ``gray``，避免同一路径重复 IO。
"""
from __future__ import annotations

import base64
import io
import logging
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple, Union

import cv2
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

# --- Stage1 缺省阈值（yaml 未写时生效；与 livehouse 场景对齐）---
_STAGE1_FALLBACK: Dict[str, float] = {
    # 拉普拉斯方差：仅在与极低 edge_ratio 同时出现时才硬拒绝（真正「糊成一片」）
    "severe_blur_lap_max": 10.0,
    "severe_blur_edge_max": 0.0012,
    # 旧字段保留语义：作为「软」惩罚档位，不再直接 reject
    "laplacian_variance_min": 30.0,
    "laplacian_variance_slight_blur": 50.0,
    "laplacian_variance_medium": 120.0,
    "laplacian_variance_high": 200.0,
    # 动感模糊：Sobel 能量比阈值 + 结构地板（有边则倾向保留给 Stage3）
    "motion_blur_ratio_threshold": 1.5,
    "motion_blur_min_edge_ratio": 0.0035,
    "artistic_motion_min_edge_ratio": 0.003,
    # Severe-blur hard reject bypass (smoke / intentional motion haze → Stage2/3)
    "artistic_severe_edge_factor": 0.35,
    "haze_escape_lap_min": 0.55,
    "haze_escape_lap_max": 8.0,
    "haze_escape_edge_min": 0.00012,
    "haze_escape_contrast_min": 5.0,
    "haze_escape_contrast_max": 14.0,
    "haze_escape_p50_min": 8.0,
    "haze_escape_p50_max": 22.0,
    "haze_escape_luma_mass_min": 0.0003,
    "haze_escape_p_range_min": 18.0,
    # 曝光：分位数 + 占比惩罚（不再硬拒）
    "highlight_percentile": 98.5,
    "shadow_percentile": 2.5,
    "highlight_mass_soft": 0.12,
    "highlight_mass_hard": 0.38,
    "shadow_mass_soft": 0.35,
    "shadow_mass_hard": 0.65,
    # 兼容旧 yaml 键名：若仍存在则覆盖 soft 档位
    "overexposed_penalty": 0.15,
    "underexposed_penalty": 0.35,
    # 对比：只减分
    "contrast_min": 10.0,
    "contrast_penalty": 20.0,
    "contrast_soft_floor": 6.0,
    # 亮度极端（全黑/全白）
    "black_p99_max": 22.0,
    "white_p01_min": 235.0,
    "blank_range_max": 5.0,
    "blank_std_max": 2.0,
    # 边缘丰富度（用于糊/动感联合判断）
    "edge_ratio_min": 0.005,
    "edge_canny_low": 45.0,
    "edge_canny_high": 135.0,
}


def _cfg(q: Mapping[str, Any], key: str) -> float:
    """Merge caller yaml with module fallbacks (float-coerced)."""
    if key in _STAGE1_FALLBACK:
        raw = q.get(key, _STAGE1_FALLBACK[key])
    else:
        raw = q.get(key)
        if raw is None:
            return 0.0
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.warning("Invalid quality_thresholds[%s]=%r, using fallback", key, raw)
        return float(_STAGE1_FALLBACK.get(key, 0.0))


def livehouse_severe_blur_escape(
    *,
    laplacian_var: float,
    edge_ratio: float,
    grad_extreme: bool,
    contrast: float,
    expo: Mapping[str, float],
    q: Mapping[str, Any],
) -> str | None:
    """If set, caller should not hard-reject for severe blur; value is escape reason."""
    artistic_floor = _cfg(q, "artistic_motion_min_edge_ratio")
    edge_artistic = artistic_floor * _cfg(q, "artistic_severe_edge_factor")
    if grad_extreme and edge_ratio >= edge_artistic:
        return "artistic_motion_structure"

    p50 = float(expo.get("p50", 0.0))
    hf = float(expo.get("highlight_frac", 0.0))
    sf = float(expo.get("shadow_frac", 0.0))
    p_range = float(expo.get("p99", 0.0)) - float(expo.get("p01", 0.0))
    if laplacian_var < _cfg(q, "haze_escape_lap_min") or laplacian_var > _cfg(q, "haze_escape_lap_max"):
        return None
    if edge_ratio < _cfg(q, "haze_escape_edge_min"):
        return None
    if contrast < _cfg(q, "haze_escape_contrast_min") or contrast > _cfg(q, "haze_escape_contrast_max"):
        return None
    if p50 < _cfg(q, "haze_escape_p50_min") or p50 > _cfg(q, "haze_escape_p50_max"):
        return None
    luma_mass = hf + sf
    if luma_mass < _cfg(q, "haze_escape_luma_mass_min") and p_range < _cfg(q, "haze_escape_p_range_min"):
        return None
    return "livehouse_haze"


class ImageProcessor:
    """OpenCV + PIL helpers: Stage1/2 metrics, composition heuristic, VLM thumbnail base64."""

    @staticmethod
    def _orientation_from_wh(w: int, h: int) -> str:
        if h <= 0:
            return "landscape"
        r = w / float(h)
        if r > 1.02:
            return "landscape"
        if r < 0.98:
            return "portrait"
        return "square"

    @staticmethod
    def _read_bgr(image_path: Union[str, Path]) -> Tuple[Optional[np.ndarray], Optional[str]]:
        """Load BGR uint8 once; supports unicode paths via imdecode."""
        p = str(image_path)
        try:
            img = cv2.imread(p)
            if img is not None:
                return img, None
            data = np.fromfile(p, dtype=np.uint8)
            img = cv2.imdecode(data, cv2.IMREAD_COLOR)
            if img is not None:
                return img, None
            return None, "Unable to read image"
        except OSError as e:
            logger.warning("OpenCV read failed for %s: %s", p, e)
            return None, f"Read error: {e}"

    @staticmethod
    def get_display_layout(
        image_path: Union[str, Path],
        img_bgr: Optional[np.ndarray] = None,
    ) -> Dict[str, Any]:
        """
        Logical width/height/orientation after EXIF transpose (browser-consistent).

        ``img_bgr`` 可选：已用 OpenCV 读入时传入，避免 PIL 再读盘。
        """
        out: Dict[str, Any] = {}
        p = str(image_path)
        try:
            from services.jpeg_exif_orientation import open_display_ready_image

            im = open_display_ready_image(p, 0)
            w, h = im.size
        except Exception as e:
            logger.warning("PIL layout read failed for %s: %s", p, e)
            if img_bgr is not None:
                h0, w0 = img_bgr.shape[:2]
                w, h = int(w0), int(h0)
            else:
                return out
        w, h = int(w), int(h)
        out["width"] = w
        out["height"] = h
        out["orientation"] = ImageProcessor._orientation_from_wh(w, h)
        return out

    @staticmethod
    def _edge_ratio(gray: np.ndarray, q: Mapping[str, Any]) -> Tuple[float, np.ndarray]:
        lo = int(max(10, _cfg(q, "edge_canny_low")))
        hi = int(max(lo + 1, _cfg(q, "edge_canny_high")))
        edges = cv2.Canny(gray, lo, hi)
        ratio = float(np.mean(edges > 0))
        return ratio, edges

    @staticmethod
    def _motion_blur_flags(gray: np.ndarray, q: Mapping[str, Any]) -> Tuple[float, bool]:
        """Sobel directional ratio; ``True`` only if ratio extreme (cheap motion cue)."""
        thr = _cfg(q, "motion_blur_ratio_threshold")
        sobelx = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
        sobely = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
        sx = float(np.std(sobelx))
        sy = float(np.std(sobely)) + 1e-5
        ratio = sx / sy
        flagged = ratio > thr or ratio < (1.0 / thr)
        return ratio, flagged

    @staticmethod
    def _exposure_percentile_scores(gray: np.ndarray, q: Mapping[str, Any]) -> Dict[str, float]:
        """
        Percentile-based highlight/shadow mass（适应舞台高光尾部拉长）。

        返回 ``highlight_frac`` / ``shadow_frac`` 等供惩罚与 debug。
        """
        g = gray.astype(np.float32)
        ph = _cfg(q, "highlight_percentile")
        ps = _cfg(q, "shadow_percentile")
        hi = float(np.percentile(g, ph))
        lo = float(np.percentile(g, ps))
        # 自适应阈值：在分位点附近取带宽，避免固定 230/30 不适应 RAW/强灯
        t_high = float(np.clip(hi + 4.0, 200.0, 255.0))
        t_low = float(np.clip(lo - 4.0, 0.0, 55.0))
        highlight_frac = float(np.mean(g >= t_high))
        shadow_frac = float(np.mean(g <= t_low))
        p1, p50, p99 = (float(x) for x in np.percentile(g, [1, 50, 99]))
        return {
            "p01": p1,
            "p50": p50,
            "p99": p99,
            "highlight_frac": highlight_frac,
            "shadow_frac": shadow_frac,
            "t_high": t_high,
            "t_low": t_low,
        }

    @staticmethod
    def _is_blank_or_extreme(gray: np.ndarray, q: Mapping[str, Any]) -> Tuple[bool, str]:
        """True only for near-uniform or empty luminance (no usable scene)."""
        std = float(gray.std())
        p1, p50, p99 = (float(x) for x in np.percentile(gray, [1, 50, 99]))
        rng = p99 - p1
        if rng <= _cfg(q, "blank_range_max") and std <= _cfg(q, "blank_std_max"):
            return True, "Flat or empty luminance (no content)"
        if p99 <= _cfg(q, "black_p99_max"):
            return True, "Near-black frame"
        if p1 >= _cfg(q, "white_p01_min") and p50 >= 245.0:
            return True, "Near-white frame"
        return False, ""

    @staticmethod
    def _laplacian_var(gray: np.ndarray) -> float:
        return float(cv2.Laplacian(gray, cv2.CV_64F).var())

    @staticmethod
    def assess_composition(
        image_path: str,
        *,
        gray: Optional[np.ndarray] = None,
        img_bgr: Optional[np.ndarray] = None,
    ) -> float:
        """
        简化构图分（0–100）：高亮区域质心到画面中心 / 三分法锚点的接近程度。

        Livehouse：主体常被 spot 打亮，用高分位阈值做「亮区 mask」是廉价可用的 proxy；
        非舞台图可能偏差，但本模块主要服务 livehouse pipeline。
        """
        try:
            if gray is None:
                if img_bgr is None:
                    img_bgr, err = ImageProcessor._read_bgr(image_path)
                    if img_bgr is None:
                        logger.warning("assess_composition: cannot read %s (%s)", image_path, err)
                        return 50.0
                gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
            h, w = gray.shape
            if h < 8 or w < 8:
                return 50.0

            thr = float(np.percentile(gray, 72))
            if thr < 1.0:
                thr = float(np.percentile(gray, 50))
            mask = (gray > thr).astype(np.uint8)
            if int(mask.sum()) < max(64, h * w // 500):
                thr = float(np.percentile(gray, 55))
                mask = (gray > thr).astype(np.uint8)
            ys, xs = np.nonzero(mask)
            if len(xs) < 10:
                return 48.0

            cx = float(xs.mean())
            cy = float(ys.mean())
            gx = (w - 1) * 0.5
            gy = (h - 1) * 0.5
            nd = float(np.hypot((cx - gx) / max(w, 1), (cy - gy) / max(h, 1)))
            thirds = [
                (w / 3.0, h / 3.0),
                (2.0 * w / 3.0, h / 3.0),
                (w / 3.0, 2.0 * h / 3.0),
                (2.0 * w / 3.0, 2.0 * h / 3.0),
            ]
            d_third = min(float(np.hypot(cx - tx, cy - ty)) for tx, ty in thirds)
            d_third_n = d_third / float(np.hypot(w, h) + 1e-6)
            anchor = float(min(nd * 1.15, d_third_n * 0.95))
            score = 100.0 * max(0.0, 1.0 - min(1.0, anchor * 2.8))

            # 轻微奖励：亮区不要太贴边（避免半个头被切）
            margin = 0.08 * min(w, h)
            if margin > 1 and (cx < margin or cy < margin or cx > w - 1 - margin or cy > h - 1 - margin):
                score = max(0.0, score - 8.0)

            return float(max(0.0, min(100.0, score)))
        except Exception as e:
            logger.warning("assess_composition failed for %s: %s", image_path, e)
            return 50.0

    @staticmethod
    def assess_image_quality(
        image_path: str,
        quality_thresholds: Dict[str, Any],
        *,
        img_bgr: Optional[np.ndarray] = None,
    ) -> Tuple[bool, Optional[str], float, Dict[str, Any]]:
        """
        Stage 1：OpenCV 快速质量与 livehouse 友好软评分。

        Returns
        -------
        passes_quality : bool
            仅对读图失败 / 真垃圾片为 False；其余为 True 以便 Stage2/3 决策。
        reason : str | None
            拒绝原因；通过时为 None（或可选 soft 说明写入 debug_info）。
        tech_score : float
            0–100 技术分（含惩罚，带 livehouse 保底避免无意义 0）。
        debug_info : dict
            指标、blur_type、``stage1_penalties``、``livehouse_bias`` 等。
        """
        q: Dict[str, Any] = dict(quality_thresholds or {})
        # 兼容旧 yaml：overexposed_threshold / underexposed_threshold 映射到分位惩罚档位
        if "highlight_mass_soft" not in q and q.get("overexposed_threshold") is not None:
            try:
                ot = float(q["overexposed_threshold"])
                q.setdefault("highlight_mass_soft", min(0.22, ot * 0.45))
                q.setdefault("highlight_mass_hard", min(0.52, ot * 1.35))
            except (TypeError, ValueError):
                pass
        if "shadow_mass_soft" not in q and q.get("underexposed_threshold") is not None:
            try:
                ut = float(q["underexposed_threshold"])
                q.setdefault("shadow_mass_soft", min(0.62, ut * 0.65))
                q.setdefault("shadow_mass_hard", min(0.88, ut * 1.08))
            except (TypeError, ValueError):
                pass

        penalties: List[str] = []
        p = str(image_path)

        try:
            if img_bgr is None:
                img_bgr, err = ImageProcessor._read_bgr(p)
            else:
                err = None
            if img_bgr is None:
                return False, err or "Unable to read image", 0.0, {"path": p, "read_error": True}

            debug_info = ImageProcessor.get_display_layout(p, img_bgr=img_bgr)
            if not debug_info.get("orientation"):
                h0, w0 = img_bgr.shape[:2]
                w, h = int(w0), int(h0)
                debug_info["width"] = w
                debug_info["height"] = h
                debug_info["orientation"] = ImageProcessor._orientation_from_wh(w, h)

            gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
            laplacian_var = ImageProcessor._laplacian_var(gray)
            edge_ratio, _edges = ImageProcessor._edge_ratio(gray, q)
            grad_ratio, grad_extreme = ImageProcessor._motion_blur_flags(gray, q)

            debug_info["laplacian_var"] = laplacian_var
            debug_info["gradient_ratio"] = grad_ratio
            debug_info["edge_ratio"] = edge_ratio

            expo = ImageProcessor._exposure_percentile_scores(gray, q)
            debug_info.update(expo)

            contrast = float(gray.std())
            debug_info["contrast"] = contrast
            mean_brightness = float(gray.mean())
            debug_info["brightness"] = mean_brightness

            blank, blank_reason = ImageProcessor._is_blank_or_extreme(gray, q)
            if blank:
                debug_info["reject"] = blank_reason
                return False, blank_reason, 0.0, debug_info

            severe_lap = _cfg(q, "severe_blur_lap_max")
            severe_edge = _cfg(q, "severe_blur_edge_max")
            motion_floor = _cfg(q, "motion_blur_min_edge_ratio")
            artistic_floor = _cfg(q, "artistic_motion_min_edge_ratio")

            stage1_severe_escape: str | None = None
            if laplacian_var < severe_lap and edge_ratio < severe_edge:
                stage1_severe_escape = livehouse_severe_blur_escape(
                    laplacian_var=laplacian_var,
                    edge_ratio=edge_ratio,
                    grad_extreme=grad_extreme,
                    contrast=contrast,
                    expo=expo,
                    q=q,
                )
                if stage1_severe_escape is None:
                    msg = "Severe blur without structure (no usable edges)"
                    debug_info["reject"] = msg
                    return False, msg, max(0.0, min(15.0, laplacian_var)), debug_info
                debug_info["stage1_severe_blur_escape"] = stage1_severe_escape

            is_motion_blur = bool(
                grad_extreme and edge_ratio < motion_floor and laplacian_var < _cfg(q, "laplacian_variance_medium")
            )
            debug_info["is_motion_blur"] = is_motion_blur

            # blur_type：供 Stage3 权重 / prompt 使用
            blur_type = "none"
            if laplacian_var < _cfg(q, "laplacian_variance_min"):
                if stage1_severe_escape in ("artistic_motion_structure", "livehouse_haze"):
                    blur_type = "artistic_motion_blur"
                elif grad_extreme and edge_ratio >= artistic_floor:
                    blur_type = "artistic_motion_blur"
                elif is_motion_blur:
                    blur_type = "motion_blur"
                else:
                    blur_type = "focus_blur"
            elif laplacian_var < _cfg(q, "laplacian_variance_slight_blur"):
                blur_type = "motion_blur" if is_motion_blur else "slight_blur"
            debug_info["blur_type"] = blur_type

            quality_score = 100.0

            # 拉普拉斯软惩罚（不再硬拒）
            if laplacian_var < _cfg(q, "laplacian_variance_min"):
                quality_score -= 22.0
                penalties.append("laplacian_low_soft")
            elif laplacian_var < _cfg(q, "laplacian_variance_slight_blur"):
                quality_score -= 14.0
                penalties.append("laplacian_slight_soft")
            elif laplacian_var < _cfg(q, "laplacian_variance_medium"):
                quality_score -= 9.0
                penalties.append("laplacian_medium_soft")
            elif laplacian_var < _cfg(q, "laplacian_variance_high"):
                quality_score -= 4.0
                penalties.append("laplacian_high_soft")

            if blur_type == "artistic_motion_blur":
                quality_score += 6.0
                penalties.append("livehouse_artistic_blur_bias")
            elif blur_type == "motion_blur" and edge_ratio >= motion_floor * 0.85:
                quality_score += 3.0
                penalties.append("livehouse_motion_structure_bias")

            # 曝光：分位数质量 + 软惩罚
            hf = expo["highlight_frac"]
            sf = expo["shadow_frac"]
            h_soft = _cfg(q, "highlight_mass_soft")
            h_hard = _cfg(q, "highlight_mass_hard")
            s_soft = _cfg(q, "shadow_mass_soft")
            s_hard = _cfg(q, "shadow_mass_hard")
            if hf > h_hard:
                quality_score -= 18.0
                penalties.append("highlight_heavy")
            elif hf > h_soft:
                quality_score -= 9.0
                penalties.append("highlight_soft")
            if sf > s_hard:
                quality_score -= 16.0
                penalties.append("shadow_heavy")
            elif sf > s_soft:
                quality_score -= 8.0
                penalties.append("shadow_soft")

            # 对比：低对比只减分（烟雾 / 背光常见）
            cmin = _cfg(q, "contrast_min")
            cpen = _cfg(q, "contrast_penalty")
            csoft = _cfg(q, "contrast_soft_floor")
            if contrast < csoft:
                quality_score -= 14.0
                penalties.append("contrast_very_low")
            elif contrast < cmin:
                quality_score -= 8.0
                penalties.append("contrast_low_soft")
            elif contrast < cpen:
                quality_score -= 4.0
                penalties.append("contrast_penalty_soft")

            # 边缘稀疏：减分不拒（烟雾天光下 edge 可能低但仍值得送 VLM）
            er_min = _cfg(q, "edge_ratio_min")
            if edge_ratio < er_min * 0.5:
                quality_score -= 12.0
                penalties.append("edge_sparse")
            elif edge_ratio < er_min:
                quality_score -= 6.0
                penalties.append("edge_low")

            # 亮度 sanity：极暗/极亮但未到 blank 的，轻度减分
            bmin = float(q.get("brightness_min", 5))
            bmax = float(q.get("brightness_max", 250))
            if mean_brightness < bmin + 8:
                quality_score -= 5.0
                penalties.append("brightness_low_soft")
            elif mean_brightness > bmax - 8:
                quality_score -= 5.0
                penalties.append("brightness_high_soft")

            quality_score = float(max(8.0, min(100.0, quality_score)))
            debug_info["stage1_penalties"] = penalties
            debug_info["livehouse_bias"] = True
            debug_info["tech_score"] = quality_score

            return True, None, quality_score, debug_info

        except Exception as e:
            logger.warning("assess_image_quality failed for %s: %s", image_path, e)
            return False, f"Check error: {type(e).__name__}: {e}", 0.0, {"path": str(image_path), "error": str(e)}

    @staticmethod
    def fast_aesthetic_assessment(
        image_path: str,
        *,
        img_bgr: Optional[np.ndarray] = None,
        gray: Optional[np.ndarray] = None,
    ) -> float:
        """
        Stage 2 快速审美 proxy（0–100）：饱和度 + 对比 + 构图启发，不做硬拒。

        与 Stage1 解耦阈值：失败时返回中性分并打日志，避免吞异常返回魔法常数。
        """
        try:
            if img_bgr is None:
                img_bgr, err = ImageProcessor._read_bgr(image_path)
                if img_bgr is None:
                    logger.warning("fast_aesthetic_assessment: read failed %s (%s)", image_path, err)
                    return 35.0
            if gray is None:
                gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)

            hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
            saturation = float(hsv[:, :, 1].mean())
            color_vibrancy = saturation / 255.0 * 100.0

            # 分位对比度比 raw std 更稳（抗极端高光像素）
            p5, p95 = (float(x) for x in np.percentile(gray, [5, 95]))
            spread = max(0.0, p95 - p5)
            technical_score = min(100.0, spread * 0.45)

            comp = ImageProcessor.assess_composition(image_path, gray=gray, img_bgr=img_bgr)

            fast_score = min(
                100.0,
                technical_score * 0.34 + color_vibrancy * 0.36 + comp * 0.22 + 10.0,
            )
            return float(max(5.0, fast_score))
        except Exception as e:
            logger.warning("fast_aesthetic_assessment failed for %s: %s", image_path, e)
            return 40.0

    @staticmethod
    def get_optimized_base64(
        image_path: str,
        max_size: Tuple[int, int] = (768, 768),
        quality: int = 85,
    ) -> str:
        """
        JPEG 缩略图 base64；RGBA / P(透明) / L 统一转 RGB，兼容 VLM 上传。

        旋转语义与画廊 `/image` 一致：EXIF 优先；EXIF 缺失时回退 RAW 同名文件的
        Orientation（strip-export 的竖拍预览否则会以横躺像素送进 VLM）。
        """
        p = str(image_path)
        try:
            from services.jpeg_exif_orientation import (
                open_display_ready_image,
                resolve_capture_rotation_degrees,
            )

            img = open_display_ready_image(p, resolve_capture_rotation_degrees(p))
            if img.mode in ("RGBA", "LA"):
                bg = Image.new("RGB", img.size, (255, 255, 255))
                bg.paste(img, mask=img.split()[-1])
                img = bg
            elif img.mode == "P":
                if "transparency" in img.info:
                    t = img.convert("RGBA")
                    bg = Image.new("RGB", t.size, (255, 255, 255))
                    bg.paste(t, mask=t.split()[-1])
                    img = bg
                else:
                    img = img.convert("RGB")
            elif img.mode == "L":
                img = img.convert("RGB")
            elif img.mode != "RGB":
                img = img.convert("RGB")
            img.thumbnail(max_size)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=int(max(1, min(100, quality))))
            return base64.b64encode(buf.getvalue()).decode("utf-8")
        except Exception as e:
            logger.warning("get_optimized_base64 failed for %s: %s", p, e)
            return ""
