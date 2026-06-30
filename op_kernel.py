import cv2
import numpy as np

# Batch-stable default. Pass seed=None to a render fn for nondeterministic grain/flavor.
DEFAULT_RENDER_SEED = 42


def _render_rng(seed: int | None, stream: int) -> np.random.Generator:
    if seed is None:
        return np.random.default_rng()
    return np.random.default_rng(int(seed) + stream * 9973)


# =============================================================================
# Optional stage FX — ghost / edge CA / psy wave (off by default; not film core)
# =============================================================================


def _stage_dream_flavor(
    img: np.ndarray,
    luminance: np.ndarray,
    x: np.ndarray,
    y: np.ndarray,
    unit_x: np.ndarray,
    unit_y: np.ndarray,
    dist_norm: np.ndarray,
    edge_ramp: np.ndarray,
    ghost_strength: float = 0.0,
    ca_strength: float = 0.0,
    psychedelic_strength: float = 0.0,
    highlight_hint: np.ndarray | None = None,
    seed: int | None = DEFAULT_RENDER_SEED,
    eps: float = 1e-6,
) -> np.ndarray:
    """Optional dream-stage FX (ghost / edge CA / edge wave). Defaults off; not for main film look."""
    if ghost_strength <= 1e-4 and ca_strength <= 1e-4 and psychedelic_strength <= 1e-4:
        return img

    h, w = img.shape[:2]
    out = img
    gx = cv2.Sobel(luminance, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(luminance, cv2.CV_32F, 0, 1, ksize=3)
    grad = np.sqrt(gx * gx + gy * gy)
    grad_norm = grad / (np.max(grad) + eps)
    edge_gate = np.power(edge_ramp, 1.15) * np.clip((grad_norm - 0.08) / 0.20, 0.0, 1.0)

    center_guard = 1.0 - np.power(np.clip(1.0 - dist_norm, 0.0, 1.0), 2.2) * 0.75
    skin = np.clip(1.0 - np.abs(img[:, :, 0] - img[:, :, 1]) / 0.13, 0.0, 1.0)
    skin *= np.clip(1.0 - np.abs(luminance - 0.42) / 0.30, 0.0, 1.0)
    subject_guard = np.clip(1.0 - 0.55 * skin * (1.0 - edge_gate), 0.35, 1.0)

    if ghost_strength > 1e-4:
        hi = highlight_hint if highlight_hint is not None else luminance
        ghost_mask = np.clip(
            np.power(grad_norm, 0.92) * np.power(edge_gate, 0.85) * np.power(np.clip(hi, 0, 1), 0.35),
            0.0,
            1.0,
        )
        shift = (0.35 + 0.55 * edge_ramp) * ghost_strength
        map_x1 = x + unit_x * shift
        map_y1 = y + unit_y * shift
        map_x2 = x - unit_x * shift * 0.5
        map_y2 = y - unit_y * shift * 0.5
        g1 = cv2.remap(out, map_x1, map_y1, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT101)
        g2 = cv2.remap(out, map_x2, map_y2, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT101)
        ghost = cv2.GaussianBlur(0.5 * g1 + 0.5 * g2, (5, 5), 0)
        alpha = np.clip(ghost_strength * ghost_mask * center_guard * subject_guard, 0.0, 0.14)[:, :, None]
        out = out * (1.0 - alpha) + ghost * alpha

    if ca_strength > 1e-4:
        ca_px = (0.12 + 0.65 * edge_ramp) * edge_gate * ca_strength * 0.32
        map_x_r = x + unit_x * ca_px
        map_y_r = y + unit_y * ca_px
        map_x_b = x - unit_x * ca_px * 0.65
        map_y_b = y - unit_y * ca_px * 0.65
        out = out.copy()
        out[:, :, 0] = cv2.remap(
            out[:, :, 0], map_x_r, map_y_r, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT101
        )
        out[:, :, 2] = cv2.remap(
            out[:, :, 2], map_x_b, map_y_b, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT101
        )

    if psychedelic_strength > 1e-4:
        lum = 0.2126 * out[:, :, 0] + 0.7152 * out[:, :, 1] + 0.0722 * out[:, :, 2]
        hi = highlight_hint if highlight_hint is not None else np.clip((lum - 0.62) / 0.38, 0.0, 1.0)
        phase = float(_render_rng(seed, 7).uniform(0.0, np.pi * 2.0))
        wave = np.sin(dist_norm * 14.0 + lum * 6.0 + phase)
        wave = np.tanh(wave * 0.9) * edge_gate * np.power(np.clip(hi, 0.0, 1.0), 0.65)
        wave *= center_guard * subject_guard
        out = out.copy()
        out[:, :, 1] += wave * 0.0028 * psychedelic_strength
        if psychedelic_strength > 0.35:
            neon = np.clip(edge_gate * np.power(edge_ramp, 1.2), 0.0, 1.0) * subject_guard
            out[:, :, 0] += neon * 0.006 * psychedelic_strength
            out[:, :, 2] += neon * 0.005 * psychedelic_strength

    return np.clip(out, 0.0, 1.0)


# =============================================================================
# Legacy / lab presets (nuclear bloom, experimental stacks)
# =============================================================================


def apply_adaptive_nuclear_bloom_v3(
    img_rgb: np.ndarray,
    bloom_strength: float = 2.0,
    halation_strength: float = 1.3,
    ca_strength: float = 0.0,
    color_grade_strength: float = 0.9,
    grain_strength: float = 0.85,
    psychedelic_strength: float = 0.0,
    ghost_strength: float = 0.0,
    seed: int | None = DEFAULT_RENDER_SEED,
) -> np.ndarray:

    img = img_rgb.astype(np.float32) / 255.0
    h, w = img.shape[:2]
    eps = 1e-6

    # ---------------------------
    # 0. Filmic + 死曝高光
    # ---------------------------
    img = img / (img + 0.55)

    # 曝光恢复
    img = np.clip(img * 1.3, 0, 1)

    # 高光爆炸（核心）
    highlight_boost = np.power(np.clip(img - 0.6, 0, 1), 1.2)
    img += highlight_boost * 0.35
    img = np.clip(img, 0, 1)

    # ---------------------------
    # 1. Luminance
    # ---------------------------
    luminance = 0.2126*img[:,:,0] + 0.7152*img[:,:,1] + 0.0722*img[:,:,2]

    # ---------------------------
    # 2. Bloom（发光版）
    # ---------------------------
    bright_threshold = np.percentile(luminance, 98.5)
    knee = max(0.02, (1.0 - bright_threshold) * 0.6)
    high_light_mask = np.clip((luminance - bright_threshold)/knee, 0,1)

    mask_seed = np.power(high_light_mask, 1.4)

    base_size = max(3, min(h,w)//50)
    bloom_1 = cv2.GaussianBlur(mask_seed, (base_size|1, base_size|1), 0)

    small = cv2.resize(mask_seed, (w//4, h//4))
    bloom_large = cv2.GaussianBlur(small, (15,15), 0)
    bloom_large = cv2.resize(bloom_large, (w,h))

    explosion = np.clip(bloom_1 + bloom_large, 0,1)
    bloom_gain = bloom_strength * (0.6 + 0.4*(1.0 - luminance))

    img_final = img + explosion[:,:,None] * bloom_gain[:,:,None]

    # ⭐ 发光增强（关键）
    img_final += explosion[:,:,None] * (0.12 + 0.1 * bloom_strength)
    img_final += (explosion**1.5)[:,:,None] * 0.15

    # ---------------------------
    # 3. 坐标
    # ---------------------------
    y, x = np.indices((h,w), dtype=np.float32)
    cx, cy = w/2, h/2

    dx = (x-cx)/cx
    dy = (y-cy)/cy
    dist = np.sqrt(dx*dx + dy*dy)
    dist_norm = dist/(np.max(dist)+eps)

    edge_ramp = np.power(dist_norm, 1.8)
    unit_x = dx/(dist+eps)
    unit_y = dy/(dist+eps)

    # ---------------------------
    # 4–5. Dream flavor（ghost / CA / wave，默认关闭）
    # ---------------------------
    img_final = _stage_dream_flavor(
        img_final,
        luminance,
        x,
        y,
        unit_x,
        unit_y,
        dist_norm,
        edge_ramp,
        ghost_strength=ghost_strength,
        ca_strength=ca_strength,
        psychedelic_strength=psychedelic_strength,
        highlight_hint=high_light_mask,
        seed=seed,
        eps=eps,
    )

    # ---------------------------
    # 6. Halation
    # ---------------------------
    img_final = _film_halation_apply(
        img_final,
        luminance,
        halation_strength,
        light_rim_hint=high_light_mask,
        eps=eps,
    )

    # ---------------------------
    # 7. Color
    # ---------------------------
    lum = 0.2126*img_final[:,:,0] + 0.7152*img_final[:,:,1] + 0.0722*img_final[:,:,2]

    shadows = np.clip((0.4-lum)/0.4,0,1)
    highlights = np.clip((lum-0.6)/0.4,0,1)

    img_final[:,:,0] += (0.1*highlights - 0.05*shadows)*color_grade_strength
    img_final[:,:,2] += (-0.05*highlights + 0.1*shadows)*color_grade_strength

    # ---------------------------
    # 8. Grain
    # ---------------------------
    img_final = _film_grain_apply(
        img_final, lum, grain_strength, h, w, seed=seed, eps=eps
    )

    # ---------------------------
    # 10. Vignette（减弱版）
    # ---------------------------
    vignette = 1 - np.power(dist_norm,1.6)*0.28
    img_final *= vignette[:,:,None]

    # ---------------------------
    # 11. Contrast Recovery（防灰）
    # ---------------------------
    mid = img_final - 0.5
    img_final = 0.5 + mid * 1.15

    # ---------------------------
    # Final
    # ---------------------------
    img_final = np.clip(img_final,0,1)
    return (img_final*255).astype(np.uint8)

def apply_adaptive_nuclear_bloom_v2(
    img_rgb: np.ndarray,
    bloom_strength: float = 1.6,
    halation_strength: float = 1.0,
    ca_strength: float = 0.0,
    color_grade_strength: float = 0.9,
    grain_strength: float = 0.8,
    psychedelic_strength: float = 0.0,
    ghost_strength: float = 0.0,
    seed: int | None = DEFAULT_RENDER_SEED,
) -> np.ndarray:

    img = img_rgb.astype(np.float32) / 255.0
    h, w = img.shape[:2]
    eps = 1e-6

    # ---------------------------
    # 0. Filmic Tone Curve
    # ---------------------------
    img = img / (img + 0.6)

    luminance = 0.2126 * img[:,:,0] + 0.7152 * img[:,:,1] + 0.0722 * img[:,:,2]
    mean_lum = np.mean(luminance)
    exposure = 0.5 / (mean_lum + 1e-6)

    img = np.clip(img * exposure, 0, 1)

    # ---------------------------
    # 1. Luminance
    # ---------------------------
    luminance = 0.2126 * img[:,:,0] + 0.7152 * img[:,:,1] + 0.0722 * img[:,:,2]

    # ---------------------------
    # 2. Bloom
    # ---------------------------
    bright_threshold = np.percentile(luminance, 98.5)
    knee = max(0.02, (1.0 - bright_threshold) * 0.6)
    high_light_mask = np.clip((luminance - bright_threshold) / knee, 0, 1)

    mask_seed = np.power(high_light_mask, 1.4)

    base_size = max(3, min(h, w)//50)
    bloom_1 = cv2.GaussianBlur(mask_seed, (base_size|1, base_size|1), 0)

    small = cv2.resize(mask_seed, (w//4, h//4))
    bloom_large = cv2.GaussianBlur(small, (15,15), 0)
    bloom_large = cv2.resize(bloom_large, (w,h))

    explosion = np.clip(bloom_1 + bloom_large, 0, 1)
    bloom_gain = bloom_strength * (0.6 + 0.4*(1.0 - luminance))

    img_final = img + explosion[:,:,None] * bloom_gain[:,:,None]
    img_final += explosion[:,:,None] * 0.08
    # ---------------------------
    # 3. 坐标（后面统一用）
    # ---------------------------
    y, x = np.indices((h,w), dtype=np.float32)
    cx, cy = w/2, h/2

    dx = (x - cx)/cx
    dy = (y - cy)/cy
    dist = np.sqrt(dx*dx + dy*dy)
    dist_norm = dist / (np.max(dist)+eps)

    edge_ramp = np.power(dist_norm, 1.8)
    unit_x = dx/(dist+eps)
    unit_y = dy/(dist+eps)

    # ---------------------------
    # 4–5. Dream flavor（默认关闭，Lab 可开）
    # ---------------------------
    img_final = _stage_dream_flavor(
        img_final,
        luminance,
        x,
        y,
        unit_x,
        unit_y,
        dist_norm,
        edge_ramp,
        ghost_strength=ghost_strength,
        ca_strength=ca_strength,
        psychedelic_strength=psychedelic_strength,
        highlight_hint=high_light_mask,
        seed=seed,
        eps=eps,
    )

    # ---------------------------
    # 6. Halation
    # ---------------------------
    img_final = _film_halation_apply(
        img_final,
        luminance,
        halation_strength,
        light_rim_hint=high_light_mask,
        eps=eps,
    )

    # ---------------------------
    # 7. Color
    # ---------------------------
    lum = 0.2126*img_final[:,:,0] + 0.7152*img_final[:,:,1] + 0.0722*img_final[:,:,2]

    shadows = np.clip((0.4-lum)/0.4,0,1)
    highlights = np.clip((lum-0.6)/0.4,0,1)

    img_final[:,:,0] += (0.1*highlights - 0.05*shadows)*color_grade_strength
    img_final[:,:,2] += (-0.05*highlights + 0.1*shadows)*color_grade_strength

    # ---------------------------
    # 8. Grain
    # ---------------------------
    img_final = _film_grain_apply(
        img_final, lum, grain_strength, h, w, seed=seed, eps=eps
    )

    # ---------------------------
    # 10. Vignette
    # ---------------------------
    vignette = 1 - np.power(dist_norm,1.6)*(0.25 + 0.2 * grain_strength)
    img_final *= vignette[:,:,None]

    # ---------------------------
    # Final
    # ---------------------------
    img_final = np.clip(img_final,0,1)
    return (img_final*255).astype(np.uint8)

def apply_adaptive_nuclear_bloom(
    img_rgb: np.ndarray,
    bloom_intensity: float = 1.85,
    softness: float = 1.05,
    vintage_strength: float = 0.9,
    psychedelic_strength: float = 0.0,
    seed: int | None = DEFAULT_RENDER_SEED,
) -> np.ndarray:
    """
    高性能图像后处理算子：实现核爆光晕、径向色散及迷幻胶片效果。
    """
    img = img_rgb.astype(np.float32) / 255.0
    h, w = img.shape[:2]
    eps = 1e-6

    # 1. 提取高光掩膜 (Luminance-based)
    luminance = 0.2126 * img[:, :, 0] + 0.7152 * img[:, :, 1] + 0.0722 * img[:, :, 2]
    bright_threshold = np.percentile(luminance, 98.5)
    knee = max(0.02, (1.0 - bright_threshold) * 0.6)
    high_light_mask = np.clip((luminance - bright_threshold) / knee, 0.0, 1.0)
    
    # 2. 生成多级 Bloom (核爆光晕)
    mask_seed = np.power(high_light_mask, 1.4)
    base_size = max(3, min(h, w) // 50)
    
    # 使用可分离卷积思路的多级模糊
    bloom_1 = cv2.GaussianBlur(mask_seed, (base_size | 1, base_size | 1), 0)
    
    # 缩放加速大半径模糊
    small_mask = cv2.resize(mask_seed, (w//4, h//4), interpolation=cv2.INTER_AREA)
    bloom_large = cv2.GaussianBlur(small_mask, (15, 15), 0)
    bloom_large = cv2.resize(bloom_large, (w, h), interpolation=cv2.INTER_LINEAR)
    
    explosion = np.clip(bloom_1 + bloom_large * softness, 0.0, 1.0)

    # 3. 颜色权重与光晕叠加
    avg_color = np.mean(img[high_light_mask > 0.5], axis=0) if np.any(high_light_mask > 0.5) else np.array([1, 1, 1])
    color_weights = avg_color / (np.max(avg_color) + eps)
    
    img_final = img.copy()
    highlight_protect = np.clip(1.0 - np.power(high_light_mask, 1.2), 0.35, 1.0)
    img_final += (
        explosion[:, :, None]
        * bloom_intensity
        * 0.5
        * color_weights[None, None, :]
        * highlight_protect[:, :, None]
    )

    # 4. 径向色散 (Radial Chromatic Aberration) - 从中心向边缘递增
    # 创建归一化的坐标网格
    y, x = np.indices((h, w), dtype=np.float32)
    center_y, center_x = h / 2, w / 2
    # 计算每个像素到中心的距离
    dx = (x - center_x) / center_x
    dy = (y - center_y) / center_y
    dist = np.sqrt(dx**2 + dy**2)
    dist_norm = np.clip(dist / (np.max(dist) + eps), 0.0, 1.0)
    edge_ramp = np.power(dist_norm, 1.8)
    unit_x = dx / (dist + eps)
    unit_y = dy / (dist + eps)

    # 4b. Dream flavor（CA / wave / 轻 fringe 合一，默认关）
    img_final = _stage_dream_flavor(
        img_final,
        luminance,
        x,
        y,
        unit_x,
        unit_y,
        dist_norm,
        edge_ramp,
        ghost_strength=0.0,
        ca_strength=psychedelic_strength,
        psychedelic_strength=psychedelic_strength,
        highlight_hint=high_light_mask,
        seed=seed,
        eps=eps,
    )

    # 5. 复古调色 (Split Toning)
    lum_final = 0.2126 * img_final[:, :, 0] + 0.7152 * img_final[:, :, 1] + 0.0722 * img_final[:, :, 2]
    shadows = np.clip((0.4 - lum_final) / 0.4, 0.0, 1.0)
    highlights = np.clip((lum_final - 0.6) / 0.4, 0.0, 1.0)
    
    # 暖高光，紫青阴影，增加复古胶片+迷幻霓虹对撞感
    img_final[:, :, 0] += (0.11 * highlights - 0.055 * shadows) * vintage_strength
    img_final[:, :, 1] += (0.018 * highlights - 0.01 * shadows) * vintage_strength
    img_final[:, :, 2] += (-0.06 * highlights + 0.12 * shadows) * vintage_strength

    # 6. Halation (边缘红晕) - 明暗交界处的红色背晕扩散
    img_final = _film_halation_apply(
        img_final,
        luminance,
        vintage_strength * 0.92,
        light_rim_hint=high_light_mask,
        eps=eps,
    )

    # 7. 胶片颗粒 (Film Grain)
    img_final = _film_grain_apply(
        img_final, lum_final, vintage_strength * 0.85, h, w, seed=seed, eps=eps
    )

    # 9.5 复古暗角 + 轻微提黑，塑造老镜头质感
    vignette = np.clip(1.0 - np.power(dist_norm, 1.65) * (0.32 + 0.28 * vintage_strength), 0.55, 1.0)
    img_final *= vignette[:, :, None]
    faded_blacks = 0.012 + 0.03 * vintage_strength
    img_final = img_final * (1.0 - faded_blacks) + faded_blacks

    img_final = np.clip(img_final, 0, 1)

    return (img_final * 255).astype(np.uint8)


def apply_livehouse_film_bk(
    img_rgb: np.ndarray,
    bloom_strength: float = 2.0,
    halation_strength: float = 1.1,
    color_strength: float = 1.0,
    grain_strength: float = 0.8,
) -> np.ndarray:

    img = img_rgb.astype(np.float32) / 255.0
    h, w = img.shape[:2]
    eps = 1e-6

    # ---------------------------
    # 0. Filmic Base（提亮但不灰）
    # ---------------------------
    base = img / (img + 0.6)
    base = np.clip(base * 1.2, 0, 1)

    luminance = 0.2126*base[:,:,0] + 0.7152*base[:,:,1] + 0.0722*base[:,:,2]

    # ---------------------------
    # 1. 稳定高光 mask（关键！）
    # ---------------------------
    bright_th = np.percentile(luminance, 97.5)

    highlight_mask = np.clip(
        (luminance - bright_th) / (0.25 + (1 - bright_th)),
        0, 1
    )

    # 防止极端爆点参与
    highlight_mask *= (luminance < 0.98)

    highlight_mask = np.power(highlight_mask, 0.8)
    highlight_mask = cv2.GaussianBlur(highlight_mask, (0,0), 6)

    # ---------------------------
    # 2. 核爆 Bloom（干净版）
    # ---------------------------
    bloom_small = cv2.GaussianBlur(highlight_mask, (0,0), 10)
    bloom_large = cv2.GaussianBlur(highlight_mask, (0,0), 25)

    bloom = bloom_small + bloom_large

    # 限幅（防脏）
    bloom = np.clip(bloom, 0, 0.65)

    energy = base + bloom[:,:,None] * bloom_strength * 0.6

    # ---------------------------
    # 3. Halation（胶片红晕）
    # ---------------------------
    halation = cv2.GaussianBlur(highlight_mask, (0,0), 12)

    energy[:,:,0] += halation * 0.12 * halation_strength
    energy[:,:,1] += halation * 0.02 * halation_strength

    # ---------------------------
    # 4. 胶片色彩（Split Toning）
    # ---------------------------
    lum = 0.2126*energy[:,:,0] + 0.7152*energy[:,:,1] + 0.0722*energy[:,:,2]

    shadows = np.clip((0.45 - lum)/0.45, 0, 1)
    highlights = np.clip((lum - 0.55)/0.45, 0, 1)

    # 暖高光 + 冷阴影
    energy[:,:,0] += (0.10*highlights - 0.05*shadows) * color_strength
    energy[:,:,1] += (0.02*highlights - 0.01*shadows) * color_strength
    energy[:,:,2] += (-0.05*highlights + 0.10*shadows) * color_strength

    # ---------------------------
    # 5. 胶片颗粒（重点）
    # ---------------------------
    energy = _film_grain_apply(
        energy, lum, grain_strength, h, w, seed=DEFAULT_RENDER_SEED, eps=eps
    )

    # ---------------------------
    # 6. 暗角（轻一点）
    # ---------------------------
    y, x = np.indices((h,w), dtype=np.float32)
    cx, cy = w/2, h/2

    dx = (x-cx)/cx
    dy = (y-cy)/cy
    dist = np.sqrt(dx*dx + dy*dy)
    dist_norm = dist/(np.max(dist)+eps)

    vignette = 1 - np.power(dist_norm, 1.6)*0.25
    energy *= vignette[:,:,None]

    # ---------------------------
    # 7. 最终保护（防炸）
    # ---------------------------
    energy = np.clip(energy, 0, 1)

    return (energy * 255).astype(np.uint8)


# =============================================================================
# Film core — shared optics (tone, bloom, halation, grain, shadow finish)
# =============================================================================


def _lh_luminance(rgb: np.ndarray) -> np.ndarray:
    return 0.2126 * rgb[:, :, 0] + 0.7152 * rgb[:, :, 1] + 0.0722 * rgb[:, :, 2]


def _subject_readability_masks(
    rgb: np.ndarray,
    lum: np.ndarray,
    h: int,
    w: int,
    eps: float = 1e-6,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Lightweight subject priors (no face model): center + mid-tone + local structure.
    Returns (subject_soft, bloom_damp, color_guard) in [0, 1].
    """
    y, x = np.indices((h, w), dtype=np.float32)
    cx, cy = w / 2, h / 2
    dist = np.sqrt(((x - cx) / cx) ** 2 + ((y - cy) / cy) ** 2)
    dist_norm = dist / (np.max(dist) + eps)
    center = 1.0 - np.power(dist_norm, 1.4) * 0.82
    mid_lum = np.clip(1.0 - np.abs(lum - 0.43) / 0.34, 0.0, 1.0) ** 1.08

    gx = cv2.Sobel(lum, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(lum, cv2.CV_32F, 0, 1, ksize=3)
    grad = np.sqrt(gx * gx + gy * gy)
    grad_norm = grad / (np.max(grad) + eps)
    structure = np.clip((grad_norm - 0.05) / 0.32, 0.0, 1.0)
    structure *= 1.0 - 0.45 * np.clip((grad_norm - 0.58) / 0.42, 0.0, 1.0)

    subject = np.clip(center * (0.40 + 0.60 * mid_lum) * (0.30 + 0.70 * structure), 0.0, 1.0)
    subject = cv2.GaussianBlur(subject, (0, 0), 12)

    skin = np.clip(1.0 - np.abs(rgb[:, :, 0] - rgb[:, :, 1]) / 0.14, 0.0, 1.0)
    skin *= np.clip(1.0 - np.abs(lum - 0.38) / 0.32, 0.0, 1.0)
    subject = np.clip(subject * 0.65 + skin * 0.55, 0.0, 1.0)
    subject = cv2.GaussianBlur(subject, (0, 0), 9)

    bloom_damp = 1.0 - 0.62 * subject * np.clip((lum - 0.38) / 0.48, 0.0, 1.0) ** 0.9
    bloom_damp = np.clip(bloom_damp, 0.38, 1.0)

    color_guard = np.clip(0.28 + 0.72 * subject, 0.28, 1.0)
    return subject, bloom_damp, color_guard


def _lh_filmic_base(img: np.ndarray, eps: float = 1e-6) -> np.ndarray:
    """Analog cinema base: soft shoulder, ink shadows, gentle exposure lock (not filter lift)."""
    base = img / (img + 0.54)
    lum = _lh_luminance(base)
    toe = np.clip((0.26 - lum) / 0.26, 0.0, 1.0) ** 1.05
    base = base + toe[:, :, None] * (0.012 - base * 0.32) * np.array(
        [0.82, 0.94, 1.08], dtype=np.float32
    )
    mid = np.clip(1.0 - np.abs(lum - 0.42) / 0.38, 0.0, 1.0)
    base = base * (1.0 - 0.04 * mid[:, :, None]) + 0.04 * mid[:, :, None] * (
        base * np.array([1.02, 0.99, 0.96], dtype=np.float32)
    )
    mean_lum = float(np.mean(_lh_luminance(base)))
    exposure = float(np.clip(0.46 / (mean_lum + eps), 0.88, 1.01))
    return np.clip(base * exposure * 0.985, 0.0, 1.0)


def _lh_stable_highlight_mask(luminance: np.ndarray, eps: float = 1e-6) -> np.ndarray:
    bright_th = np.percentile(luminance, 97.5)
    knee = 0.28 + (1.0 - bright_th) * 0.55
    mask = np.clip((luminance - bright_th) / (knee + eps), 0.0, 1.0)
    mask *= luminance < 0.97
    mask = np.power(mask, 0.92)
    return cv2.GaussianBlur(mask, (0, 0), 5.5)


def _lh_soft_highlight_compress(rgb: np.ndarray, eps: float = 1e-6) -> np.ndarray:
    """Pre-bloom shoulder: smoothstep top-end so bloom spreads color, not white clip."""
    lum = _lh_luminance(rgb)
    t = np.clip((lum - 0.56) / 0.40, 0.0, 1.0)
    t = t * t * (3.0 - 2.0 * t)
    blend = t[:, :, None]
    compressed = rgb / (rgb + 0.15 * blend + eps)
    return rgb * (1.0 - blend * 0.84) + compressed * (blend * 0.84)


def _lh_percentile_highlight_seed(luminance: np.ndarray, eps: float = 1e-6) -> np.ndarray:
    """v3-style adaptive knee (percentile + soft knee), without highlight_boost."""
    bright_th = float(np.percentile(luminance, 98.0))
    knee = max(0.02, (1.0 - bright_th) * 0.58)
    hl = np.clip((luminance - bright_th) / (knee + eps), 0.0, 1.0)
    hl *= luminance < 0.96
    seed = np.power(hl, 1.38)
    return cv2.GaussianBlur(seed, (0, 0), 4.0)


def _lh_edge_strength_map(lum: np.ndarray, eps: float = 1e-6) -> np.ndarray:
    gx = cv2.Sobel(lum, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(lum, cv2.CV_32F, 0, 1, ksize=3)
    grad = np.sqrt(gx * gx + gy * gy)
    edge = grad / (np.max(grad) + eps)
    return cv2.GaussianBlur(edge, (0, 0), 2.2)


def _lh_atmospheric_instability_fields(
    h: int,
    w: int,
    seed: int | None,
    eps: float = 1e-6,
) -> dict[str, np.ndarray]:
    """Low-frequency diffusion / glow / smoke variation (deterministic; not uniform Gaussian glow)."""

    def _lf(stream: int, sigma: float) -> np.ndarray:
        n = _render_rng(seed, stream).normal(0, 1, (h, w)).astype(np.float32)
        return cv2.GaussianBlur(n, (0, 0), sigma)

    s1 = max(18.0, min(h, w) / 20.0)
    s2 = max(42.0, min(h, w) / 9.0)
    s3 = max(9.0, min(h, w) / 52.0)
    lf1 = _lf(20, s1)
    lf2 = _lf(21, s2)
    lf3 = _lf(22, s3)

    ch, cw = max(5, h // 56), max(5, w // 56)
    chunk = _render_rng(seed, 23).normal(0, 1, (ch, cw)).astype(np.float32)
    chunk = cv2.resize(chunk, (w, h), interpolation=cv2.INTER_LINEAR)
    chunk = cv2.GaussianBlur(chunk, (0, 0), 3.2)

    diffusion = 1.0 + 0.21 * lf1 + 0.14 * lf2 + 0.09 * chunk
    diffusion = np.clip(diffusion, 0.62, 1.38).astype(np.float32)

    glow_r = np.clip(1.0 + 0.17 * lf1 - 0.07 * lf2 + 0.07 * _lf(24, s3), 0.70, 1.34)
    glow_g = np.clip(1.0 + 0.11 * lf2 - 0.06 * lf1 + 0.05 * chunk, 0.72, 1.30)
    glow_b = np.clip(1.0 - 0.09 * lf1 + 0.15 * lf2 + 0.08 * lf3, 0.68, 1.36)
    edge_smoke = np.clip(0.48 + 0.52 * (lf3 + 0.32 * chunk), 0.0, 1.0).astype(np.float32)
    drift = _lf(26, s2 * 1.05)

    return {
        "diffusion": diffusion,
        "glow_r": glow_r,
        "glow_g": glow_g,
        "glow_b": glow_b,
        "edge_smoke": edge_smoke,
        "drift": drift,
    }


def _memory_cinema_fields(h: int, w: int, seed: int | None, eps: float = 1e-6) -> dict[str, np.ndarray]:
    """Extended instability pack for irregular air, drift, decay (one build per frame)."""
    f = _lh_atmospheric_instability_fields(h, w, seed, eps)
    ch, cw = max(4, h // 44), max(4, w // 44)
    patch = _render_rng(seed, 27).normal(0, 1, (ch, cw)).astype(np.float32)
    patch = cv2.resize(patch, (w, h), interpolation=cv2.INTER_LINEAR)
    patch = cv2.GaussianBlur(patch, (0, 0), 2.6)
    f["haze_patch"] = np.clip(0.40 + 0.60 * np.abs(patch), 0.0, 1.0).astype(np.float32)

    y_i, x_i = np.indices((h, w), dtype=np.float32)
    leak = 0.5 + 0.22 * ((x_i / max(w - 1, 1)) - 0.5) + 0.18 * f["drift"]
    f["leak"] = np.clip(leak, 0.0, 1.0).astype(np.float32)

    def _tp(stream: int, sigma: float) -> np.ndarray:
        n = _render_rng(seed, stream).normal(0, 1, (h, w)).astype(np.float32)
        return cv2.GaussianBlur(n, (0, 0), sigma)

    f["temporal_a"] = _tp(28, max(14.0, min(h, w) / 24.0))
    f["temporal_b"] = _tp(29, max(36.0, min(h, w) / 10.0))
    decay = 0.55 * f["drift"] + 0.45 * f["temporal_b"]
    f["decay"] = cv2.GaussianBlur(decay.astype(np.float32), (0, 0), max(20.0, min(h, w) / 18.0))
    return f


def _cinema_fields_or_build(
    h: int,
    w: int,
    seed: int | None,
    eps: float,
    cinema_fields: dict[str, np.ndarray] | None,
) -> dict[str, np.ndarray]:
    if cinema_fields is not None:
        return cinema_fields
    return _memory_cinema_fields(h, w, seed, eps)


def _irregular_scalar_diffuse(
    src: np.ndarray,
    sigma_base: float,
    fields: dict[str, np.ndarray],
    eps: float = 1e-6,
    clip_hi: float = 1.0,
) -> np.ndarray:
    """Patchy multi-radius diffuse — breaks single-Gaussian continuity."""
    d = fields["diffusion"]
    p = fields["haze_patch"]
    smoke = fields["edge_smoke"]
    # cv2.GaussianBlur needs scalar sigma; spatial variation stays in w0/w1/w2 below.
    d_ref = float(np.mean(d))
    p_ref = float(np.mean(p))
    s0 = max(0.75, sigma_base * (0.46 + 0.30 * d_ref))
    s1 = max(1.0, sigma_base * (0.84 + 0.50 * d_ref))
    s2 = max(1.4, sigma_base * (1.22 + 0.68 * d_ref) * (0.78 + 0.38 * p_ref))
    b0 = cv2.GaussianBlur(src, (0, 0), s0)
    b1 = cv2.GaussianBlur(src, (0, 0), s1)
    b2 = cv2.GaussianBlur(src, (0, 0), s2)
    w0 = np.clip(1.12 - d, 0.0, 1.0) * np.clip(1.05 - p, 0.0, 1.0)
    w2 = np.clip(d - 0.76, 0.0, 1.0) * p * smoke
    w1 = np.clip(1.0 - w0 - w2, 0.12, 1.0)
    blend = (b0 * w0 + b1 * w1 + b2 * w2) / (w0 + w1 + w2 + eps)
    return np.clip(blend, 0.0, clip_hi)


def _lh_pseudo_depth_planes(
    lum: np.ndarray,
    subject_soft: np.ndarray,
    dist_norm: np.ndarray,
    h: int,
    w: int,
    eps: float = 1e-6,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Foreground / midground / background weights for layered atmosphere."""
    inv_sub = cv2.GaussianBlur(np.clip(1.0 - subject_soft, 0.0, 1.0), (0, 0), max(12.0, min(h, w) / 32.0))
    blur_n = cv2.GaussianBlur(lum, (0, 0), 2.4)
    blur_w = cv2.GaussianBlur(lum, (0, 0), 17.0)
    local_contrast = np.clip(np.abs(blur_n - blur_w) * 2.6, 0.0, 1.0)

    depth = (
        0.33 * dist_norm
        + 0.37 * inv_sub
        + 0.22 * np.power(np.clip(1.0 - lum, 0.0, 1.0), 0.68)
        - 0.12 * local_contrast * inv_sub
    )
    depth = cv2.GaussianBlur(np.clip(depth, 0.0, 1.0), (0, 0), max(9.0, min(h, w) / 50.0))

    fg = np.power(np.clip(1.0 - depth, 0.0, 1.0), 1.55) * np.clip(0.32 + 0.68 * subject_soft, 0.0, 1.0)
    bg = np.power(np.clip(depth, 0.0, 1.0), 0.95) * (0.52 + 0.48 * dist_norm)
    bg = cv2.GaussianBlur(bg, (0, 0), max(11.0, min(h, w) / 45.0))
    mid = np.clip(1.0 - fg - bg * 0.88, 0.0, 1.0)
    mid = cv2.GaussianBlur(mid * (0.25 + 0.75 * local_contrast), (0, 0), 6.0)
    return fg.astype(np.float32), mid.astype(np.float32), bg.astype(np.float32)


def _lh_multiscale_bloom_apply(
    base: np.ndarray,
    h: int,
    w: int,
    bloom_strength: float,
    bloom_damp: np.ndarray | None = None,
    subject_soft: np.ndarray | None = None,
    seed: int | None = DEFAULT_RENDER_SEED,
    cinema_fields: dict[str, np.ndarray] | None = None,
    eps: float = 1e-6,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Micro / lens / atmospheric bloom hierarchy with edge-aware masks and screen composite.
    Practical light glow + lens softness + haze; not global fog.
    """
    lum = _lh_luminance(base)
    inst = _cinema_fields_or_build(h, w, seed, eps, cinema_fields)
    glow_scale = np.stack([inst["glow_r"], inst["glow_g"], inst["glow_b"]], axis=-1)
    edge_strength = _lh_edge_strength_map(lum, eps)
    highlight_seed = _lh_percentile_highlight_seed(lum, eps)

    bloom_mask = highlight_seed * (1.0 - edge_strength * 0.40)
    bloom_mask = np.clip(bloom_mask, 0.0, 1.0)
    lum_hi = np.power(np.clip((lum - 0.42) / 0.58, 0.0, 1.0), 0.88)
    bloom_mask *= lum_hi

    micro_sigma = max(1.0, min(h, w) / 700.0)
    lens_sigma = max(3.2, min(h, w) / 195.0)
    atmo_sigma = max(11.0, min(h, w) / 50.0)

    micro_src = np.power(highlight_seed, 1.42) * (1.0 - edge_strength * 0.22)
    lens_src = bloom_mask
    dist_norm = _radial_dist_norm(h, w, eps)
    depth_haze = np.power(dist_norm, 1.12) * np.power(np.clip(1.0 - lum, 0.0, 1.0), 0.62)
    depth_haze = cv2.GaussianBlur(depth_haze, (0, 0), max(8.0, min(h, w) / 55.0))

    if subject_soft is not None:
        atmo_src = bloom_mask * (1.0 - 0.62 * subject_soft) * (0.62 + 0.38 * depth_haze)
    else:
        atmo_src = bloom_mask * (1.0 - edge_strength * 0.12) * (0.70 + 0.30 * depth_haze)

    micro = _irregular_scalar_diffuse(micro_src, micro_sigma, inst, eps=eps, clip_hi=0.42)
    lens = _irregular_scalar_diffuse(lens_src, lens_sigma, inst, eps=eps, clip_hi=0.48)

    small = cv2.resize(
        atmo_src * inst["haze_patch"],
        (max(1, w // 4), max(1, h // 4)),
        interpolation=cv2.INTER_AREA,
    )
    atmo = cv2.GaussianBlur(small, (0, 0), 15)
    atmo = cv2.resize(atmo, (w, h), interpolation=cv2.INTER_LINEAR)
    atmo = _irregular_scalar_diffuse(atmo, atmo_sigma * 0.34, inst, eps=eps, clip_hi=0.38)
    atmo_tight = _irregular_scalar_diffuse(atmo_src, atmo_sigma * 0.20, inst, eps=eps, clip_hi=0.36)
    mix = np.clip((inst["diffusion"] - 0.80) / 0.50, 0.0, 1.0) * inst["haze_patch"]
    atmo = np.clip(atmo_tight * (1.0 - mix) + atmo * mix, 0.0, 0.40)

    micro = np.clip(micro * 0.88 * (0.92 + 0.08 * inst["diffusion"]), 0.0, 0.42)
    lens = np.clip(
        lens * 0.80 * inst["diffusion"] * (1.0 + 0.16 * inst["edge_smoke"] * edge_strength),
        0.0,
        0.48,
    )
    atmo = np.clip(atmo * 0.68 * inst["diffusion"], 0.0, 0.36)

    sel = highlight_seed > 0.16
    if np.any(sel):
        avg_color = np.mean(base[sel], axis=0).astype(np.float32)
    else:
        avg_color = np.array([1.0, 0.94, 0.88], dtype=np.float32)
    avg_color = avg_color / (float(np.max(avg_color)) + eps)
    tint = 0.68 * avg_color + 0.32 * np.array([1.02, 0.95, 0.90], dtype=np.float32)
    tint = tint / (float(np.max(tint)) + eps)
    cool_shift = np.array([0.98, 1.02, 1.05], dtype=np.float32)

    air = np.power(np.clip(1.0 - lum, 0.0, 1.0), 0.52) * (0.78 + 0.22 * depth_haze)
    core_damp = 1.0 - 0.68 * np.power(np.clip((lum - 0.70) / 0.30, 0.0, 1.0), 1.08)
    if bloom_damp is not None:
        core_damp = core_damp * bloom_damp

    strength = bloom_strength * 0.26 * air * core_damp
    out = base.copy()
    for field, weight, layer_gain in (
        (micro, 0.26, 1.04),
        (lens, 0.28, 0.92),
        (atmo, 0.16, 0.78),
    ):
        amt = field * weight * strength * layer_gain * inst["diffusion"]
        layer_tint = tint[None, None, :] * (1.0 - 0.10 * depth_haze[:, :, None]) + cool_shift * (
            0.10 * depth_haze[:, :, None]
        )
        bloom_rgb = amt[:, :, None] * layer_tint * glow_scale
        out = np.clip(out + bloom_rgb * (1.0 - out), 0.0, 1.0)

    smoke_src = atmo_src * inst["edge_smoke"] * np.power(highlight_seed, 0.85)
    smoke_small = cv2.resize(
        smoke_src,
        (max(1, w // 5), max(1, h // 5)),
        interpolation=cv2.INTER_AREA,
    )
    smoke = cv2.GaussianBlur(smoke_small, (0, 0), 12.0)
    smoke = cv2.resize(smoke, (w, h), interpolation=cv2.INTER_LINEAR)
    smoke = cv2.GaussianBlur(smoke, (0, 0), atmo_sigma * 0.45)
    smoke_amt = smoke * strength * 0.18 * inst["diffusion"]
    out = np.clip(
        out + smoke_amt[:, :, None] * layer_tint * glow_scale * (1.0 - out),
        0.0,
        1.0,
    )

    blur_base = cv2.GaussianBlur(base, (0, 0), 2.0)
    detail = base - blur_base
    preserve = np.clip(0.12 + 0.18 * (1.0 - edge_strength), 0.10, 0.32)
    if subject_soft is not None:
        preserve *= 1.0 + 0.72 * subject_soft
    out = np.clip(out + detail * preserve[:, :, None] * 1.35, 0.0, 1.0)

    return out, highlight_seed


def _lh_highlight_shoulder(rgb: np.ndarray, eps: float = 1e-6) -> np.ndarray:
    """Luminance-aware soft clip on upper range (roll-off, not blow-up)."""
    lum = _lh_luminance(rgb)
    t = np.clip((lum - 0.62) / 0.38, 0.0, 1.0)
    t = t * t * (3.0 - 2.0 * t)
    blend = t[:, :, None]
    compressed = rgb / (rgb + 0.13 * blend + eps)
    return rgb * (1.0 - blend * 0.78) + compressed * (blend * 0.78)


def _halation_spatial_noise(h: int, w: int, seed: int | None, eps: float = 1e-6) -> np.ndarray:
    """Low-frequency variation: smoke / lens contamination / emulsion instability."""
    rng = _render_rng(seed, 8)
    n = rng.normal(0.0, 1.0, (h, w)).astype(np.float32)
    lf = cv2.GaussianBlur(n, (0, 0), max(10.0, min(h, w) / 42.0))
    lf = lf / (float(np.max(np.abs(lf))) + eps)
    return np.clip(1.0 + 0.20 * lf, 0.74, 1.26)


def _halation_tint_from_light(
    rgb: np.ndarray,
    hal_seed: np.ndarray,
    eps: float = 1e-6,
) -> np.ndarray:
    """Adaptive contamination hue from local highlight color (not fixed red fringe)."""
    wsum = float(np.sum(hal_seed)) + eps
    avg_r = float(np.sum(rgb[:, :, 0] * hal_seed)) / wsum
    avg_g = float(np.sum(rgb[:, :, 1] * hal_seed)) / wsum
    avg_b = float(np.sum(rgb[:, :, 2] * hal_seed)) / wsum
    warmth = float(np.clip((avg_r - avg_b) / 0.28, -1.0, 1.0))
    magenta = float(np.clip((avg_r + avg_b - 2.0 * avg_g) / 0.38, 0.0, 1.0))
    blue_led = float(np.clip((avg_b - avg_r) / 0.26, 0.0, 1.0))

    warm_t = np.array([1.08, 0.40, 0.14], dtype=np.float32)
    mag_t = np.array([1.02, 0.30, 0.78], dtype=np.float32)
    blue_t = np.array([0.38, 0.62, 1.10], dtype=np.float32)
    neutral_t = np.array([1.02, 0.48, 0.32], dtype=np.float32)

    w_warm = max(0.0, warmth) * (1.0 - 0.65 * blue_led)
    w_mag = magenta * (1.0 - 0.5 * max(0.0, warmth))
    w_blue = blue_led * (1.0 - 0.4 * max(0.0, warmth))
    w_neu = max(0.0, 1.0 - w_warm - w_mag - w_blue)

    tint = warm_t * w_warm + mag_t * w_mag + blue_t * w_blue + neutral_t * w_neu
    tint = tint / (float(np.max(tint)) + eps)
    return tint.astype(np.float32)


def _film_halation_channel_blur(seed: np.ndarray, sigma: float) -> np.ndarray:
    near = cv2.GaussianBlur(seed, (0, 0), max(1.0, sigma * 0.32))
    far = cv2.GaussianBlur(seed, (0, 0), sigma)
    return np.clip(0.38 * near + 0.62 * far, 0.0, 0.50)


def _film_halation_apply(
    rgb: np.ndarray,
    lum_edge: np.ndarray,
    halation_strength: float,
    light_rim_hint: np.ndarray | None = None,
    subject_soft: np.ndarray | None = None,
    seed: int | None = DEFAULT_RENDER_SEED,
    eps: float = 1e-6,
) -> np.ndarray:
    """Film halation: emulsion-layer blur (R>G>B), spatial noise, light-color contamination."""
    h, w = rgb.shape[:2]
    gx = cv2.Sobel(lum_edge, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(lum_edge, cv2.CV_32F, 0, 1, ksize=3)
    grad = np.sqrt(gx * gx + gy * gy)
    grad_norm = grad / (np.max(grad) + eps)

    blur_fine = cv2.GaussianBlur(lum_edge, (0, 0), 5)
    blur_wide = cv2.GaussianBlur(lum_edge, (0, 0), 13)
    direction = np.clip(lum_edge - blur_fine, 0.0, 1.0)
    edge_contrast = np.clip(np.abs(lum_edge - blur_wide), 0.0, 1.0)

    rim_band = np.clip((lum_edge - 0.40) / 0.32, 0.0, 1.0) * np.clip(
        (0.88 - lum_edge) / 0.30, 0.0, 1.0
    )

    if light_rim_hint is not None:
        light_rim = np.power(np.clip(light_rim_hint, 0.0, 1.0), 0.50)
    else:
        light_rim = direction

    hal_seed = (
        np.power(grad_norm, 0.90)
        * np.power(direction, 1.0)
        * np.power(edge_contrast, 0.80)
        * rim_band
        * (0.25 + 0.75 * light_rim)
    )
    hal_seed = np.clip(hal_seed, 0.0, 1.0)

    grad_gate = np.clip((grad_norm - 0.10) / 0.22, 0.0, 1.0)
    hal_seed *= grad_gate

    hot_core = np.power(np.clip((lum_edge - 0.80) / 0.20, 0.0, 1.0), 1.25)
    hal_seed *= 1.0 - 0.88 * hot_core

    hal_r = _film_halation_channel_blur(hal_seed, 19.0)
    hal_g = _film_halation_channel_blur(hal_seed, 12.0)
    hal_b = _film_halation_channel_blur(hal_seed, 6.5)

    lum_out = _lh_luminance(rgb)
    protect = np.clip(1.0 - np.power(np.clip(lum_out, 0.0, 1.0), 1.55), 0.12, 1.0)
    hal_r *= protect
    hal_g *= protect
    hal_b *= protect

    redness = np.clip(rgb[:, :, 0] - rgb[:, :, 1] * 0.88, 0.0, 1.0)
    red_guard = 1.0 - 0.50 * np.power(np.clip(redness / 0.22, 0.0, 1.0), 1.0)
    hal_r *= red_guard
    hal_g *= red_guard
    hal_b *= red_guard
    if subject_soft is not None:
        sub = 1.0 - 0.55 * subject_soft * (1.0 - grad_gate * 0.35)
        hal_r *= sub
        hal_g *= sub
        hal_b *= sub

    spatial = _halation_spatial_noise(h, w, seed, eps)
    strength = halation_strength * spatial

    tint = _halation_tint_from_light(rgb, hal_seed, eps)
    gain_r, gain_g, gain_b = float(tint[0]), float(tint[1]), float(tint[2])

    out = rgb.copy()
    out[:, :, 0] += hal_r * 0.068 * strength * gain_r
    out[:, :, 1] += hal_g * 0.018 * strength * gain_g
    out[:, :, 2] += hal_b * 0.036 * strength * gain_b
    return np.clip(out, 0.0, 1.0)


def _film_grain_apply(
    rgb: np.ndarray,
    lum: np.ndarray,
    grain_strength: float,
    h: int,
    w: int,
    shadows: np.ndarray | None = None,
    subject_soft: np.ndarray | None = None,
    seed: int | None = DEFAULT_RENDER_SEED,
    eps: float = 1e-6,
) -> np.ndarray:
    """
    Vision3-style pushed negative grain: tone-dependent particle size, per-channel
    emulsion, density drift, and grain scatter into bloom (not flat overlay).
    """
    s = float(np.clip(grain_strength, 0.0, 2.0))
    sigma_base = 0.0075 + 0.0155 * s

    if shadows is not None:
        shadow_w = np.clip(shadows, 0.0, 1.0) ** 0.88
    else:
        shadow_w = np.clip((0.48 - lum) / 0.48, 0.0, 1.0) ** 0.92
    highlight_w = np.clip((lum - 0.56) / 0.44, 0.0, 1.0) ** 1.02
    mid_w = np.clip(1.0 - np.abs(lum - 0.44) / 0.36, 0.0, 1.0) ** 1.15

    emul_sigma = max(18.0, min(h, w) / 22.0)
    drift_sigma = max(42.0, min(h, w) / 9.5)
    emulsion = cv2.GaussianBlur(
        _render_rng(seed, 5).normal(0, 1, (h, w)).astype(np.float32), (0, 0), emul_sigma
    )
    drift = cv2.GaussianBlur(
        _render_rng(seed, 6).normal(0, 1, (h, w)).astype(np.float32), (0, 0), drift_sigma
    )
    density_mod = 1.0 + (0.17 * s) * emulsion + (0.085 * s) * drift
    density_mod = cv2.GaussianBlur(density_mod.astype(np.float32), (0, 0), 6.0)

    chunk_h = max(4, h // 48)
    chunk_w = max(4, w // 48)
    chunk_seed = _render_rng(seed, 7).normal(0, 1, (chunk_h, chunk_w)).astype(np.float32)
    chunk_tex = cv2.resize(chunk_seed, (w, h), interpolation=cv2.INTER_LINEAR)
    chunk_tex = cv2.GaussianBlur(chunk_tex, (0, 0), 2.2)
    density_mod *= 1.0 + 0.11 * s * chunk_tex

    def _channel_grain(stream_a: int, stream_b: int, coarse_bias: float) -> np.ndarray:
        n_a = _render_rng(seed, stream_a).normal(0, 1, (h, w)).astype(np.float32)
        n_b = _render_rng(seed, stream_b).normal(0, 1, (h, w)).astype(np.float32)
        fine = n_a
        med = cv2.GaussianBlur(n_a, (0, 0), 1.25 + 0.35 * coarse_bias)
        coarse = cv2.GaussianBlur(n_b, (0, 0), 3.6 + 0.9 * coarse_bias)
        jumbo = cv2.GaussianBlur(n_b, (0, 0), 7.0 + 1.4 * coarse_bias)
        tex = (
            highlight_w * fine * (0.88 + 0.08 * coarse_bias)
            + mid_w * med * (0.52 + 0.06 * coarse_bias)
            + shadow_w * coarse * (0.74 + 0.10 * coarse_bias)
            + np.power(shadow_w, 1.12) * jumbo * (0.42 + 0.14 * coarse_bias)
        )
        return tex * density_mod

    grain_r = _channel_grain(10, 11, 0.0)
    grain_g = _channel_grain(12, 13, 0.12)
    grain_b = _channel_grain(14, 15, 0.28)

    luma_tex = 0.36 * grain_r + 0.34 * grain_g + 0.30 * grain_b
    luma_tex = cv2.GaussianBlur(luma_tex, (0, 0), 0.42)

    hi_fade = np.clip((lum - 0.62) / 0.38, 0.0, 1.0) ** 1.05
    crush_guard = np.clip((0.06 - lum) / 0.06, 0.0, 1.0)
    weight = (mid_w + 0.22 * shadow_w) * (1.0 - 0.78 * hi_fade) * (1.0 - 0.38 * crush_guard)
    weight = np.clip(weight * density_mod, 0.0, 1.18)
    if subject_soft is not None:
        weight *= 1.0 - 0.38 * subject_soft

    delta_lum = luma_tex * sigma_base * weight
    lum_safe = np.maximum(lum, eps)
    new_lum = np.clip(lum + delta_lum, 0.0, 1.0)
    out = rgb * (new_lum / lum_safe)[:, :, None]

    chroma_sigma = sigma_base * 0.46
    sub_ch = 1.0 - 0.52 * subject_soft if subject_soft is not None else 1.0
    d_r = (grain_r * chroma_sigma - delta_lum * 0.36) * weight * sub_ch
    d_g = (grain_g * chroma_sigma * 0.92 - delta_lum * 0.34) * weight * sub_ch
    d_b = (grain_b * chroma_sigma * 1.08 - delta_lum * 0.32) * weight * sub_ch
    out[:, :, 0] += d_r * 0.58
    out[:, :, 1] += d_g * 0.24
    out[:, :, 2] += d_b * (-0.31)

    bloom_gate = np.power(np.clip((lum - 0.34) / 0.58, 0.0, 1.0), 0.82)
    scatter_lum = np.maximum(delta_lum, 0.0) * bloom_gate
    scatter_fine = cv2.GaussianBlur(scatter_lum, (0, 0), 1.15)
    scatter_wide = cv2.GaussianBlur(scatter_lum, (0, 0), 5.8)
    scatter_mix = highlight_w * scatter_fine * 0.48 + shadow_w * scatter_wide * 0.32
    scatter_mix += 0.18 * s * cv2.GaussianBlur(
        np.maximum(grain_r, 0.0) * chroma_sigma * weight, (0, 0), 2.4
    )

    scatter_rgb = scatter_mix[:, :, None] * np.array(
        [0.11, 0.088, 0.072], dtype=np.float32
    ) * (0.55 + 0.45 * s)
    out = np.clip(out + scatter_rgb * (1.0 - out), 0.0, 1.0)

    neg_tail = np.minimum(delta_lum, 0.0) * shadow_w * (0.22 * s)
    out = np.clip(out + neg_tail[:, :, None] * np.array([-0.04, 0.0, 0.05], dtype=np.float32), 0.0, 1.0)

    return np.clip(out, 0.0, 1.0)


# =============================================================================
# Style profiles — color narrative (applied after film core optics)
# =============================================================================


def _lh_atmospheric_depth_apply(
    rgb: np.ndarray,
    lum: np.ndarray,
    h: int,
    w: int,
    subject_soft: np.ndarray,
    dist_norm: np.ndarray,
    strength: float = 1.0,
    highlight_seed: np.ndarray | None = None,
    seed: int | None = DEFAULT_RENDER_SEED,
    cinema_fields: dict[str, np.ndarray] | None = None,
    eps: float = 1e-6,
) -> np.ndarray:
    """
    Layered memory atmosphere: fg clarity, mid dirty edge glow, bg thick haze,
    emotional defocus + drift (liquid air, not uniform diffusion).
    """
    s = float(np.clip(strength, 0.0, 1.25))
    inst = _cinema_fields_or_build(h, w, seed, eps, cinema_fields)
    fg, mid, bg = _lh_pseudo_depth_planes(lum, subject_soft, dist_norm, h, w, eps)
    glow_rgb = np.stack([inst["glow_r"], inst["glow_g"], inst["glow_b"]], axis=-1)

    grad = _lh_edge_strength_map(lum, eps)
    sub = cv2.GaussianBlur(np.clip(subject_soft, 0.0, 1.0), (0, 0), 7.0)
    subject_ring = mid * grad * (0.45 + 0.55 * sub)

    out = rgb.copy()
    gray = out.mean(axis=2, keepdims=True)

    fg_clear = fg * (1.0 - 0.18 * np.clip(inst["diffusion"] - 1.0, 0.0, 0.35))
    centered = out - 0.5
    out = np.clip(0.5 + centered * (1.0 + 0.082 * s * fg_clear[:, :, None]), 0.0, 1.0)

    hi_seed = highlight_seed if highlight_seed is not None else np.power(np.clip(lum, 0, 1), 1.4)
    edge_glow = subject_ring * inst["edge_smoke"] * np.power(hi_seed, 0.9)
    out = np.clip(out + edge_glow[:, :, None] * glow_rgb * (0.034 * s) * (1.0 - out), 0.0, 1.0)

    sub_keep = cv2.GaussianBlur(np.clip(subject_soft, 0.0, 1.0), (0, 0), 9.0)
    bg_haze = bg * inst["diffusion"] * (0.62 + 0.38 * np.power(1.0 - lum, 0.65))
    bg_haze = np.clip(bg_haze, 0.0, 1.0) * (1.0 - sub_keep * 0.88)
    sat = 1.0 - 0.022 * s * bg_haze
    out = gray + (out - gray) * sat[:, :, None]
    collapse = 1.0 - 0.010 * s * bg_haze * np.clip(inst["diffusion"], 0.75, 1.35)
    out = np.clip(0.5 + (out - 0.5) * collapse[:, :, None], 0.0, 1.0)

    warm_practical = np.clip((lum - 0.30) / 0.52, 0.0, 1.0) ** 0.92
    air_tint = (
        np.array([1.04, 0.95, 0.88], dtype=np.float32)[None, None, :] * warm_practical[:, :, None]
        + np.array([0.92, 0.98, 1.07], dtype=np.float32)[None, None, :]
        * (1.0 - warm_practical[:, :, None])
    )
    bloom_lift = bg_haze * hi_seed * (0.022 * s) * inst["diffusion"]
    out = np.clip(out + bloom_lift[:, :, None] * air_tint * glow_rgb * (1.0 - out), 0.0, 1.0)

    patch_pocket = bg * inst["haze_patch"] * inst["edge_smoke"] * np.power(hi_seed, 0.85)
    pocket_rgb = patch_pocket[:, :, None] * np.array([0.014, 0.011, 0.010], dtype=np.float32) * s
    out = np.clip(out + pocket_rgb * (1.0 - out) * inst["leak"][:, :, None], 0.0, 1.0)

    shadow_w = np.clip((0.44 - lum) / 0.44, 0.0, 1.0) ** 1.06
    dirt = shadow_w * inst["edge_smoke"] * (0.5 + 0.5 * mid)
    out += dirt[:, :, None] * np.array([-0.009, 0.005, 0.012], dtype=np.float32) * s
    return np.clip(out, 0.0, 1.0)


def _lh_tone_masks(lum: np.ndarray, eps: float = 1e-6) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    shadows = np.clip((0.50 - lum) / 0.50, 0.0, 1.0)
    shadows = cv2.GaussianBlur(shadows, (0, 0), 5)
    mids = np.clip(1.0 - np.abs(lum - 0.45) / 0.34, 0.0, 1.0) ** 1.12
    mids = cv2.GaussianBlur(mids, (0, 0), 6)
    highlights = np.clip((lum - 0.62) / 0.36, 0.0, 1.0)
    highlights = cv2.GaussianBlur(highlights, (0, 0), 5)
    return shadows, mids, highlights


def apply_parametric_grade(img_rgb: np.ndarray, adj: object) -> np.ndarray:
    """Bake VLM-driven Lightroom-style deltas onto an RGB uint8 image.

    ``adj`` is a :class:`services.edit_adjustments.EditAdjustments` (or any object
    exposing the same float fields). All work happens in linear-ish 0..1 space;
    tone-region edits reuse :func:`_lh_tone_masks`. No-op when ``adj`` is inactive.
    """
    is_active = getattr(adj, "is_active", None)
    if callable(is_active) and not is_active():
        return img_rgb

    img = img_rgb.astype(np.float32) / 255.0

    # 1. Exposure (EV stops) — multiplicative gain.
    ev = float(getattr(adj, "exposure", 0.0) or 0.0)
    if abs(ev) > 1e-3:
        img = img * (2.0 ** ev)
    img = np.clip(img, 0.0, 1.0)

    # 2. White balance: temp (R up / B down) and tint (magenta vs green).
    temp = float(getattr(adj, "temp", 0.0) or 0.0) / 100.0
    tint = float(getattr(adj, "tint", 0.0) or 0.0) / 100.0
    if abs(temp) > 1e-3:
        img[:, :, 0] += temp * 0.12
        img[:, :, 2] -= temp * 0.12
    if abs(tint) > 1e-3:
        img[:, :, 0] += tint * 0.06
        img[:, :, 2] += tint * 0.06
        img[:, :, 1] -= tint * 0.08
    img = np.clip(img, 0.0, 1.0)

    # 3. Tone-region edits: shadows / highlights / whites / blacks / blacks.
    shadows = float(getattr(adj, "shadows", 0.0) or 0.0) / 100.0
    highlights = float(getattr(adj, "highlights", 0.0) or 0.0) / 100.0
    whites = float(getattr(adj, "whites", 0.0) or 0.0) / 100.0
    blacks = float(getattr(adj, "blacks", 0.0) or 0.0) / 100.0
    if any(abs(v) > 1e-3 for v in (shadows, highlights, whites, blacks)):
        lum = _lh_luminance(img)
        sh_mask, _mid, hi_mask = _lh_tone_masks(lum)
        # Whites/blacks bite harder at the extremes than shadows/highlights.
        wh_mask = np.clip((lum - 0.78) / 0.22, 0.0, 1.0)
        bk_mask = np.clip((0.22 - lum) / 0.22, 0.0, 1.0)
        delta = (
            sh_mask * (shadows * 0.30)
            + hi_mask * (highlights * 0.30)
            + wh_mask * (whites * 0.22)
            + bk_mask * (blacks * 0.22)
        )
        img += delta[:, :, None]
        img = np.clip(img, 0.0, 1.0)

    # 4. Contrast — S-curve around mid-grey.
    contrast = float(getattr(adj, "contrast", 0.0) or 0.0) / 100.0
    if abs(contrast) > 1e-3:
        img = np.clip((img - 0.5) * (1.0 + contrast * 0.6) + 0.5, 0.0, 1.0)

    # 5. Clarity — local midtone contrast via unsharp on luminance.
    clarity = float(getattr(adj, "clarity", 0.0) or 0.0) / 100.0
    if abs(clarity) > 1e-3:
        lum = _lh_luminance(img)
        blur = cv2.GaussianBlur(lum, (0, 0), 12)
        local = lum - blur
        mid_w = np.clip(1.0 - np.abs(lum - 0.45) / 0.45, 0.0, 1.0)
        img += (local * mid_w * (clarity * 1.4))[:, :, None]
        img = np.clip(img, 0.0, 1.0)

    # 6. Saturation + vibrance (vibrance protects already-saturated pixels).
    saturation = float(getattr(adj, "saturation", 0.0) or 0.0) / 100.0
    vibrance = float(getattr(adj, "vibrance", 0.0) or 0.0) / 100.0
    if abs(saturation) > 1e-3 or abs(vibrance) > 1e-3:
        gray = _lh_luminance(img)[:, :, None]
        if abs(saturation) > 1e-3:
            img = gray + (img - gray) * (1.0 + saturation)
            img = np.clip(img, 0.0, 1.0)
        if abs(vibrance) > 1e-3:
            mx = img.max(axis=2, keepdims=True)
            mn = img.min(axis=2, keepdims=True)
            cur_sat = (mx - mn) / (mx + 1e-6)
            weight = np.clip(1.0 - cur_sat, 0.0, 1.0)
            img = gray + (img - gray) * (1.0 + vibrance * weight)
            img = np.clip(img, 0.0, 1.0)

    return (np.clip(img, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)


def _film_color_cinema_stage(
    rgb: np.ndarray,
    color_strength: float,
    color_guard: np.ndarray | None = None,
    eps: float = 1e-6,
) -> np.ndarray:
    """
    HK / Doyle stage grade: teal shadow air, warm practical mids, cream highlights,
    restrained magenta in mixed light — movie still, not copper filter.
    """
    lum = _lh_luminance(rgb)
    shadows, mids, highlights = _lh_tone_masks(lum, eps)
    cs = float(np.clip(color_strength, 0.0, 1.35))

    out = rgb.copy()
    out[:, :, 0] += (-0.022 * shadows + 0.048 * mids + 0.028 * highlights) * cs
    out[:, :, 1] += (0.008 * shadows + 0.030 * mids + 0.022 * highlights) * cs
    out[:, :, 2] += (0.020 * shadows - 0.028 * mids + 0.010 * highlights) * cs

    teal_shadow = shadows[:, :, None] * np.array([-0.012, 0.014, 0.022], dtype=np.float32) * cs
    warm_mid = mids[:, :, None] * np.array([0.018, 0.008, -0.014], dtype=np.float32) * cs
    cream_hi = highlights[:, :, None] * np.array([0.012, 0.010, 0.006], dtype=np.float32) * cs
    out += teal_shadow + warm_mid + cream_hi

    mixed = np.clip((rgb[:, :, 0] + rgb[:, :, 2] - 2.0 * rgb[:, :, 1]) / 0.42, 0.0, 1.0)
    mixed *= mids * (1.0 - highlights) * 0.55 * cs
    out[:, :, 0] += mixed * 0.010
    out[:, :, 2] += mixed * 0.014

    skin = np.clip(1.0 - np.abs(rgb[:, :, 0] - rgb[:, :, 1]) / 0.13, 0.0, 1.0)
    skin *= np.clip(1.0 - np.abs(lum - 0.40) / 0.30, 0.0, 1.0)
    guard = 1.0 - 0.30 * skin
    if color_guard is not None:
        guard = np.clip(guard * color_guard, 0.26, 1.0)
    out = rgb + (out - rgb) * guard[:, :, None]

    gray = out.mean(axis=2, keepdims=True)
    out = gray + (out - gray) * (1.0 - 0.042 * shadows[:, :, None] * cs)
    hi_roll = np.clip((lum - 0.68) / 0.32, 0.0, 1.0) ** 1.1
    gray2 = out.mean(axis=2, keepdims=True)
    cream = gray2 * np.array([1.0, 0.99, 0.97], dtype=np.float32)
    out = out * (1.0 - 0.035 * hi_roll[:, :, None] * cs) + cream * (0.035 * hi_roll[:, :, None] * cs)
    return np.clip(out, 0.0, 1.0)


def _film_color_copper_brown(
    rgb: np.ndarray,
    color_strength: float,
    color_guard: np.ndarray | None = None,
    eps: float = 1e-6,
) -> np.ndarray:
    """Alias: livehouse product uses cinema-stage grade (legacy name kept)."""
    return _film_color_cinema_stage(rgb, color_strength, color_guard, eps)


def _film_color_faded_cream(rgb: np.ndarray, eps: float = 1e-6) -> np.ndarray:
    """Variant film_cold_v2: faded cream — soft mids, lifted shadows, one hue family."""
    lum = _lh_luminance(rgb)
    shadows, mids, highlights = _lh_tone_masks(lum, eps)

    out = rgb.copy()
    out[:, :, 0] += (-0.012 * shadows + 0.038 * mids + 0.048 * highlights)
    out[:, :, 1] += (-0.006 * shadows + 0.036 * mids + 0.052 * highlights)
    out[:, :, 2] += (0.008 * shadows - 0.018 * mids + 0.028 * highlights)
    out += mids[:, :, None] * np.array([0.010, 0.012, 0.022], dtype=np.float32)

    skin = np.clip(1.0 - np.abs(rgb[:, :, 0] - rgb[:, :, 1]) / 0.14, 0.0, 1.0)
    skin *= np.clip(1.0 - np.abs(lum - 0.42) / 0.32, 0.0, 1.0)
    out = rgb + (out - rgb) * (1.0 - 0.32 * skin[:, :, None])

    gray = out.mean(axis=2, keepdims=True)
    out = gray + (out - gray) * 0.93
    return np.clip(out, 0.0, 1.0)


def _film_chromatic_shadow_render(
    rgb: np.ndarray,
    lum: np.ndarray,
    shadows: np.ndarray,
    dist_norm: np.ndarray,
    seed: int | None = DEFAULT_RENDER_SEED,
    strength: float = 1.0,
    cinema_fields: dict[str, np.ndarray] | None = None,
    eps: float = 1e-6,
) -> np.ndarray:
    """
    Tungsten / Vision3 underexposed shadows: purple-brown-blue blacks, drift, breathing,
    hidden texture — emotional darkness with something still visible inside.
    """
    h, w = lum.shape[:2]
    s = float(np.clip(strength, 0.0, 1.35))
    out = rgb.copy()

    deep = np.clip((0.44 - lum) / 0.44, 0.0, 1.0) ** 0.92
    deep = cv2.GaussianBlur(deep, (0, 0), 5.5)
    ink = np.power(deep, 1.15) * shadows

    fields = _cinema_fields_or_build(h, w, seed, eps, cinema_fields)
    drift_a = fields["drift"]
    edge_smoke = fields["edge_smoke"]
    drift_b = cv2.GaussianBlur(
        _render_rng(seed, 30).normal(0, 1, (h, w)).astype(np.float32), (0, 0), max(28.0, min(h, w) / 14.0)
    )
    drift_c = cv2.GaussianBlur(
        _render_rng(seed, 31).normal(0, 1, (h, w)).astype(np.float32), (0, 0), max(52.0, min(h, w) / 8.0)
    )

    purple_w = np.clip(0.38 + 0.32 * drift_a + 0.18 * drift_b, 0.0, 1.0) * ink
    brown_w = np.clip(0.34 + 0.28 * drift_b - 0.12 * drift_a, 0.0, 1.0) * ink * (0.65 + 0.35 * dist_norm)
    blue_w = np.clip(0.30 + 0.26 * drift_c + 0.14 * dist_norm, 0.0, 1.0) * ink * (
        0.55 + 0.45 * np.power(1.0 - lum, 0.8)
    )
    mix = purple_w + brown_w + blue_w + eps
    purple_w = purple_w / mix
    brown_w = brown_w / mix
    blue_w = blue_w / mix

    purple_blk = np.array([0.118, 0.062, 0.152], dtype=np.float32)
    brown_blk = np.array([0.142, 0.098, 0.068], dtype=np.float32)
    blue_blk = np.array([0.058, 0.078, 0.128], dtype=np.float32)
    contam = (
        purple_w[:, :, None] * purple_blk
        + brown_w[:, :, None] * brown_blk
        + blue_w[:, :, None] * blue_blk
    )
    out = np.clip(out + contam * (0.11 * s) * (1.0 - out), 0.0, 1.0)

    breath = cv2.GaussianBlur(
        _render_rng(seed, 32).normal(0, 1, (h, w)).astype(np.float32), (0, 0), max(22.0, min(h, w) / 16.0)
    )
    breath = np.clip(0.5 + 0.5 * breath, 0.0, 1.0)
    chroma_pulse = 1.0 + (breath - 0.5) * 0.14 * s * ink
    gray = out.mean(axis=2, keepdims=True)
    out = gray + (out - gray) * chroma_pulse[:, :, None]

    fine_r = _render_rng(seed, 33).normal(0, 1, (h, w)).astype(np.float32)
    fine_g = _render_rng(seed, 34).normal(0, 1, (h, w)).astype(np.float32)
    fine_b = _render_rng(seed, 35).normal(0, 1, (h, w)).astype(np.float32)
    fine_r = cv2.GaussianBlur(fine_r, (0, 0), 1.1)
    fine_g = cv2.GaussianBlur(fine_g, (0, 0), 1.2)
    fine_b = cv2.GaussianBlur(fine_b, (0, 0), 1.0)
    noise_rgb = np.stack([fine_r, fine_g, fine_b], axis=-1)
    noise_rgb *= (0.0085 * s) * ink[:, :, None]

    blur_w = cv2.GaussianBlur(lum, (0, 0), 14.0)
    hidden_tex = (lum - blur_w) * (0.55 + 0.45 * edge_smoke)
    hidden_tex = cv2.GaussianBlur(hidden_tex.astype(np.float32), (0, 0), 2.8)
    tex_rgb = hidden_tex[:, :, None] * np.array([0.04, 0.035, 0.05], dtype=np.float32) * s * ink[:, :, None]

    cloud = cv2.GaussianBlur(
        _render_rng(seed, 36).normal(0, 1, (h, w)).astype(np.float32), (0, 0), max(6.0, min(h, w) / 70.0)
    )
    cloud = cv2.GaussianBlur(cloud, (0, 0), max(14.0, min(h, w) / 22.0))
    cloud = cloud[:, :, None] * np.array([-0.012, 0.006, 0.014], dtype=np.float32) * (0.022 * s) * ink[:, :, None]

    out = np.clip(out + noise_rgb + tex_rgb + cloud, 0.0, 1.0)

    lum_now = _lh_luminance(out)
    lift_mask = np.clip((0.16 - lum_now) / 0.16, 0.0, 1.0) ** 0.88 * ink
    floor_rgb = contam * (0.85 + 0.15 * breath[:, :, None]) + np.array(
        [0.032, 0.028, 0.038], dtype=np.float32
    ) * (1.0 - 0.35 * purple_w[:, :, None])
    out = out * (1.0 - lift_mask[:, :, None] * 0.28 * s) + floor_rgb * (lift_mask[:, :, None] * 0.28 * s)
    return np.clip(out, 0.0, 1.0)


def _film_shadow_density_finish(
    rgb: np.ndarray,
    lum: np.ndarray,
    shadows: np.ndarray,
    dist_norm: np.ndarray,
    seed: int | None = DEFAULT_RENDER_SEED,
    cinema_fields: dict[str, np.ndarray] | None = None,
    chromatic_strength: float = 1.0,
    eps: float = 1e-6,
) -> np.ndarray:
    """Toe density + chromatic shadows + soft vignette (no digital crush-to-gray)."""
    out = rgb.copy()

    deep = np.clip((0.26 - lum) / 0.26, 0.0, 1.0) ** 1.02
    deep = cv2.GaussianBlur(deep, (0, 0), 4)
    toe = deep * (1.0 - lum) * 0.022
    out += toe[:, :, None] * np.array([0.88, 0.96, 1.06], dtype=np.float32)

    shadow_zone = np.clip((0.42 - lum) / 0.42, 0.0, 1.0) ** 1.05
    shadow_zone = cv2.GaussianBlur(shadow_zone, (0, 0), 5)
    mid_keep = 1.0 - np.clip((lum - 0.36) / 0.26, 0.0, 1.0)
    centered = out - 0.5
    out = 0.5 + centered * (1.0 + 0.042 * shadow_zone[:, :, None] * mid_keep[:, :, None])

    gray = out.mean(axis=2, keepdims=True)
    out = gray + (out - gray) * (1.0 - 0.045 * shadows[:, :, None])

    out = _film_chromatic_shadow_render(
        out,
        lum,
        shadows,
        dist_norm,
        seed=seed,
        strength=chromatic_strength,
        cinema_fields=cinema_fields,
        eps=eps,
    )

    vig_base = 1.0 - np.power(dist_norm, 1.48) * 0.18
    vig_shadow = 1.0 - np.power(dist_norm, 1.42) * 0.08 * shadows
    out *= np.clip(vig_base * vig_shadow, 0.62, 1.0)[:, :, None]

    lum2 = _lh_luminance(out)
    hold = np.clip((0.09 - lum2) / 0.09, 0.0, 1.0) ** 1.05
    hold = cv2.GaussianBlur(hold, (0, 0), 3.5) * shadows
    out = np.clip(
        out * (1.0 - hold[:, :, None] * 0.18)
        + hold[:, :, None] * np.array([0.034, 0.030, 0.042], dtype=np.float32),
        0.0,
        1.0,
    )

    return np.clip(out, 0.0, 1.0)


def _apply_filmic_base(img: np.ndarray, eps: float = 1e-6) -> np.ndarray:
    """Film core: tone curve + pre-bloom highlight shoulder."""
    base = _lh_filmic_base(img, eps)
    base = _lh_soft_highlight_compress(base, eps)
    return _lh_highlight_shoulder(base, eps)


def _apply_bloom_stack(
    base: np.ndarray,
    h: int,
    w: int,
    bloom_strength: float,
    bloom_damp: np.ndarray | None = None,
    subject_soft: np.ndarray | None = None,
    seed: int | None = DEFAULT_RENDER_SEED,
    cinema_fields: dict[str, np.ndarray] | None = None,
    eps: float = 1e-6,
) -> tuple[np.ndarray, np.ndarray]:
    """Film core: edge-aware multi-scale bloom (micro / lens / atmospheric)."""
    energy, highlight_seed = _lh_multiscale_bloom_apply(
        base,
        h,
        w,
        bloom_strength,
        bloom_damp=bloom_damp,
        subject_soft=subject_soft,
        seed=seed,
        cinema_fields=cinema_fields,
        eps=eps,
    )
    energy = _lh_highlight_shoulder(energy, eps)
    return energy, highlight_seed


def _apply_halation(
    rgb: np.ndarray,
    lum_edge: np.ndarray,
    halation_strength: float,
    light_rim_hint: np.ndarray | None = None,
    subject_soft: np.ndarray | None = None,
    seed: int | None = DEFAULT_RENDER_SEED,
    eps: float = 1e-6,
) -> np.ndarray:
    return _film_halation_apply(
        rgb,
        lum_edge,
        halation_strength,
        light_rim_hint,
        subject_soft,
        seed,
        eps,
    )


def _apply_film_grain(
    rgb: np.ndarray,
    lum: np.ndarray,
    grain_strength: float,
    h: int,
    w: int,
    shadows: np.ndarray | None = None,
    subject_soft: np.ndarray | None = None,
    seed: int | None = DEFAULT_RENDER_SEED,
    eps: float = 1e-6,
) -> np.ndarray:
    return _film_grain_apply(
        rgb, lum, grain_strength, h, w, shadows=shadows, subject_soft=subject_soft, seed=seed, eps=eps
    )


def _apply_split_tone(
    rgb: np.ndarray,
    style: str,
    color_strength: float = 1.0,
    color_guard: np.ndarray | None = None,
    eps: float = 1e-6,
) -> np.ndarray:
    """Style profile hook: color narrative only."""
    if style == "copper_brown":
        return _film_color_copper_brown(rgb, color_strength, color_guard, eps)
    if style == "faded_cream":
        return _film_color_faded_cream(rgb, eps)
    raise ValueError(f"unknown film style profile: {style}")


def _emotional_focus_hierarchy_maps(
    rgb: np.ndarray,
    lum: np.ndarray,
    subject_soft: np.ndarray,
    dist_norm: np.ndarray,
    h: int,
    w: int,
    eps: float = 1e-6,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Heuristic memory fragments (no face model): lips / mic / glass / body melt / periphery.
    """
    y, x = np.indices((h, w), dtype=np.float32)
    cx, cy = w * 0.5, h * 0.46
    rx = max(w * 0.30, 8.0)
    ry = max(h * 0.27, 8.0)
    r_face = np.sqrt(((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2)
    face_core = np.exp(-np.power(r_face, 2.0) * 1.65) * np.clip(subject_soft, 0.0, 1.0)
    face_core = cv2.GaussianBlur(face_core.astype(np.float32), (0, 0), 5.0)

    skin = np.clip(1.0 - np.abs(rgb[:, :, 0] - rgb[:, :, 1]) / 0.13, 0.0, 1.0)
    skin *= np.clip(1.0 - np.abs(lum - 0.39) / 0.32, 0.0, 1.0)

    gx = cv2.Sobel(lum, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(lum, cv2.CV_32F, 0, 1, ksize=3)
    grad = np.sqrt(gx * gx + gy * gy)
    grad_norm = grad / (np.max(grad) + eps)
    fine = np.abs(lum - cv2.GaussianBlur(lum, (0, 0), 1.7))

    y_below = np.clip((y - cy) / max(h * 0.07, 1.0) + 0.42, 0.0, 1.0)
    y_lip_cap = np.clip(1.0 - (y - cy) / max(h * 0.16, 1.0), 0.0, 1.0)
    lips = face_core * skin * y_below * y_lip_cap
    lips *= np.clip((fine - 0.025) / 0.13, 0.0, 1.0) ** 0.82
    lips = cv2.GaussianBlur(lips.astype(np.float32), (0, 0), 3.5)

    y_upper = np.clip(1.0 - (y - (cy - h * 0.05)) / max(h * 0.11, 1.0), 0.0, 1.0)
    dark_eye = face_core * y_upper * np.clip((0.30 - lum) / 0.30, 0.0, 1.0) ** 0.9
    glass_edge = dark_eye * np.clip(grad_norm * 1.15, 0.0, 1.0) * (1.0 - dark_eye * 0.35)
    glass_edge = cv2.GaussianBlur(glass_edge.astype(np.float32), (0, 0), 2.8)

    vert = np.clip(np.abs(gx) / (grad + eps) - 0.52, 0.0, 1.0)
    vert *= np.clip((grad_norm - 0.10) / 0.35, 0.0, 1.0)
    below_face = np.clip((y - (cy + h * 0.06)) / max(h * 0.32, 1.0), 0.0, 1.0)
    center_w = np.clip(1.0 - np.abs(x - cx) / max(w * 0.24, 1.0), 0.0, 1.0)
    mic = vert * subject_soft * below_face * center_w * (0.55 + 0.45 * (1.0 - skin))
    mic = cv2.GaussianBlur(mic.astype(np.float32), (0, 0), 4.0)

    ear_fade = face_core * np.clip(np.abs(x - cx) / max(w * 0.26, 1.0) - 0.42, 0.0, 1.0) ** 1.05
    body_melt = subject_soft * np.clip((y - (cy + h * 0.10)) / max(h * 0.40, 1.0), 0.0, 1.0) ** 1.12
    body_melt = cv2.GaussianBlur(body_melt.astype(np.float32), (0, 0), 8.0)

    inv_sub = 1.0 - cv2.GaussianBlur(np.clip(subject_soft, 0.0, 1.0), (0, 0), 10.0)
    peripheral = np.clip(0.55 * dist_norm + 0.45 * inv_sub - 0.35 * face_core, 0.0, 1.0)
    peripheral = cv2.GaussianBlur(peripheral.astype(np.float32), (0, 0), 7.0)

    return lips, mic, glass_edge, ear_fade, body_melt, peripheral, face_core


def _apply_emotional_focus_drift(
    rgb: np.ndarray,
    subject_soft: np.ndarray,
    dist_norm: np.ndarray,
    h: int,
    w: int,
    strength: float = 1.0,
    seed: int | None = DEFAULT_RENDER_SEED,
    cinema_fields: dict[str, np.ndarray] | None = None,
    eps: float = 1e-6,
) -> np.ndarray:
    """
    Clarity-first: performer stays sharp; only far background gets a light memory soften.
    """
    s = float(np.clip(strength, 0.0, 1.25))
    if s <= 1e-4:
        return rgb

    sub = cv2.GaussianBlur(np.clip(subject_soft, 0.0, 1.0), (0, 0), 7.0)
    inv = np.clip(1.0 - sub, 0.0, 1.0)
    bg_only = np.clip(inv * dist_norm * 0.65 + inv * 0.35, 0.0, 1.0) ** 1.1
    bg_only = cv2.GaussianBlur(bg_only.astype(np.float32), (0, 0), 8.0)

    soft = cv2.GaussianBlur(rgb, (0, 0), max(1.8, min(h, w) / 380.0))
    blend = np.clip(bg_only * (0.10 * s), 0.0, 0.12)
    out = np.clip(rgb * (1.0 - blend[:, :, None]) + soft * blend[:, :, None], 0.0, 1.0)

    lum = _lh_luminance(rgb)
    lips, mic, _, _, _, _, face_core = _emotional_focus_hierarchy_maps(
        rgb, lum, subject_soft, dist_norm, h, w, eps
    )
    micro = out - cv2.GaussianBlur(out, (0, 0), max(1.2, min(h, w) / 450.0))
    fragments = np.clip(lips * 1.05 + mic * 0.9 + face_core * 0.4, 0.0, 1.0)
    out = np.clip(out + micro * fragments[:, :, None] * (0.06 * s), 0.0, 1.0)
    return out


def _apply_controlled_imperfection(
    rgb: np.ndarray,
    lum: np.ndarray,
    fields: dict[str, np.ndarray],
    highlight_seed: np.ndarray | None,
    h: int,
    w: int,
    strength: float,
    eps: float = 1e-6,
) -> np.ndarray:
    """Subtle emotional loss of control: local overflow, collapse, washout — not glitch."""
    s = strength
    out = rgb.copy()
    hi = highlight_seed if highlight_seed is not None else np.power(np.clip(lum, 0.0, 1.0), 1.3)
    mid = np.clip(1.0 - np.abs(lum - 0.44) / 0.38, 0.0, 1.0) ** 1.08

    overflow = hi * fields["haze_patch"] * np.clip((lum - 0.54) / 0.46, 0.0, 1.0) * fields["diffusion"]
    overflow_rgb = overflow[:, :, None] * np.array([0.019, 0.016, 0.013], dtype=np.float32) * s
    out = np.clip(out + overflow_rgb * (1.0 - out), 0.0, 1.0)

    collapse = fields["haze_patch"] * fields["leak"] * (1.0 - mid) * 0.085 * s
    out = np.clip(0.5 + (out - 0.5) * (1.0 - collapse[:, :, None]), 0.0, 1.0)

    wash = np.power(hi, 1.15) * fields["leak"] * (0.028 * s)
    gray = out.mean(axis=2, keepdims=True)
    out = np.clip(out * (1.0 - wash[:, :, None]) + gray * wash[:, :, None], 0.0, 1.0)

    ta = fields["temporal_a"] - float(np.mean(fields["temporal_a"]))
    exposure = 1.0 + ta * (0.013 * s) * mid
    out = np.clip(out * exposure[:, :, None], 0.0, 1.0)

    flood = fields["edge_smoke"] * fields["haze_patch"] * np.power(1.0 - lum, 0.55) * (0.022 * s)
    flood_rgb = flood[:, :, None] * np.array([1.02, 0.97, 0.94], dtype=np.float32)
    out = np.clip(out + flood_rgb * (1.0 - out), 0.0, 1.0)
    return out


def _apply_temporal_visual_drift(
    rgb: np.ndarray,
    lum: np.ndarray,
    fields: dict[str, np.ndarray],
    h: int,
    w: int,
    strength: float,
    eps: float = 1e-6,
) -> np.ndarray:
    """Almost-invisible temporal instability — memory edges drifting."""
    s = strength
    out = rgb.copy()
    mid = np.clip(1.0 - np.abs(lum - 0.44) / 0.38, 0.0, 1.0) ** 1.1
    vibr = (fields["temporal_a"] - fields["temporal_b"]) * 0.5

    out[:, :, 0] += vibr * (0.0026 * s) * mid
    out[:, :, 2] -= vibr * (0.0022 * s) * mid
    out[:, :, 1] += fields["drift"] * (0.0014 * s) * mid

    edge = _lh_edge_strength_map(lum, eps)
    hi = np.clip((lum - 0.50) / 0.50, 0.0, 1.0)
    edge_pulse = edge * hi * vibr * fields["edge_smoke"]
    edge_rgb = edge_pulse[:, :, None] * np.array([0.011, 0.009, 0.008], dtype=np.float32) * s
    out = np.clip(out + edge_rgb * (1.0 - out), 0.0, 1.0)

    density = 1.0 + vibr * (0.004 * s) * mid + fields["temporal_b"] * (0.002 * s) * (1.0 - mid)
    out = np.clip(out * density[:, :, None], 0.0, 1.0)
    return out


def _apply_emotional_decay_render(
    rgb: np.ndarray,
    lum: np.ndarray,
    fields: dict[str, np.ndarray],
    dist_norm: np.ndarray,
    strength: float,
    eps: float = 1e-6,
) -> np.ndarray:
    """Time-soaked emotional residue — faded structure, dirty lifted blacks, tonal fade."""
    s = strength
    out = rgb.copy()
    decay = fields["decay"]
    mid = np.clip(1.0 - np.abs(lum - 0.45) / 0.36, 0.0, 1.0)

    edge_fade = np.power(dist_norm, 1.18) * (1.0 - lum) * decay
    gray = out.mean(axis=2, keepdims=True)
    out = out * (1.0 - 0.022 * s * edge_fade[:, :, None]) + gray * (0.022 * s * edge_fade[:, :, None])

    residue = mid * decay * (0.038 * s)
    out[:, :, 0] += residue * 0.010
    out[:, :, 2] += residue * 0.014
    out[:, :, 1] -= residue * 0.004

    fade = 1.0 - 0.035 * s * decay * mid
    out = np.clip(out * fade[:, :, None], 0.0, 1.0)

    shadow_lift = np.clip((0.22 - lum) / 0.22, 0.0, 1.0) * decay * (0.018 * s)
    out += shadow_lift[:, :, None] * np.array([0.038, 0.032, 0.048], dtype=np.float32)
    return np.clip(out, 0.0, 1.0)


def _apply_atmospheric_unpredictability(
    rgb: np.ndarray,
    lum: np.ndarray,
    fields: dict[str, np.ndarray],
    subject_soft: np.ndarray,
    dist_norm: np.ndarray,
    highlight_seed: np.ndarray | None,
    h: int,
    w: int,
    strength: float,
    eps: float = 1e-6,
) -> np.ndarray:
    """Living air: patchy accumulation, directional leak, asymmetric softness."""
    s = strength
    out = rgb.copy()
    hi = highlight_seed if highlight_seed is not None else np.power(np.clip(lum, 0.0, 1.0), 1.25)
    inv = np.clip(1.0 - subject_soft, 0.0, 1.0)

    accum = fields["haze_patch"] * fields["diffusion"] * inv * hi * (0.055 * s)
    soft_asym = cv2.GaussianBlur(
        out, (0, 0), sigmaX=max(3.5, w / 210.0), sigmaY=max(1.4, h / 420.0)
    )
    out = np.clip(out * (1.0 - accum[:, :, None]) + soft_asym * accum[:, :, None], 0.0, 1.0)

    leak = fields["leak"] * hi * dist_norm * (0.022 * s)
    out[:, :, 0] += leak * 0.012
    out[:, :, 2] += leak * 0.010
    out[:, :, 1] -= leak * 0.003

    grad = _lh_edge_strength_map(lum, eps)
    edge_soft = grad * inv * fields["haze_patch"] * (0.042 * s)
    smeared = cv2.GaussianBlur(out, (0, 0), max(2.2, min(h, w) / 280.0))
    out = np.clip(out * (1.0 - edge_soft[:, :, None]) + smeared * edge_soft[:, :, None], 0.0, 1.0)
    return out


def _apply_emotional_memory_engine(
    rgb: np.ndarray,
    lum: np.ndarray,
    subject_soft: np.ndarray,
    dist_norm: np.ndarray,
    highlight_seed: np.ndarray | None,
    h: int,
    w: int,
    seed: int | None = DEFAULT_RENDER_SEED,
    strength: float = 1.0,
    cinema_fields: dict[str, np.ndarray] | None = None,
    clarity_first: bool = True,
    eps: float = 1e-6,
) -> np.ndarray:
    """
    Emotional Cinematic Memory Engine — irregular air, controlled imperfection,
    temporal drift, decay residue, living atmosphere (fading recall, not filters).
    """
    fields = _cinema_fields_or_build(h, w, seed, eps, cinema_fields)
    if clarity_first:
        s = float(np.clip(strength, 0.0, 1.2)) * 0.08
        out = _apply_emotional_decay_render(rgb, lum, fields, dist_norm, s, eps)
        return np.clip(out, 0.0, 1.0)

    s = float(np.clip(strength, 0.0, 1.2)) * 0.26
    out = rgb
    out = _apply_controlled_imperfection(out, lum, fields, highlight_seed, h, w, s, eps)
    out = _apply_temporal_visual_drift(out, _lh_luminance(out), fields, h, w, s, eps)
    out = _apply_emotional_decay_render(out, _lh_luminance(out), fields, dist_norm, s, eps)
    out = _apply_atmospheric_unpredictability(
        out,
        _lh_luminance(out),
        fields,
        subject_soft,
        dist_norm,
        highlight_seed,
        h,
        w,
        s,
        eps,
    )
    out = _apply_memory_decay(
        out, h, w, seed=seed, strength=0.38, cinema_fields=fields, eps=eps
    )
    return np.clip(out, 0.0, 1.0)


def _apply_memory_decay(
    rgb: np.ndarray,
    h: int,
    w: int,
    seed: int | None = DEFAULT_RENDER_SEED,
    strength: float = 1.0,
    cinema_fields: dict[str, np.ndarray] | None = None,
    blur_artifacts: bool = True,
    eps: float = 1e-6,
) -> np.ndarray:
    """
    Subtle memory degradation: chroma drift, faint ghost, breathing, density wobble,
    unstable highlight edges — years-later recall, not VHS/glitch.
    """
    s = float(np.clip(strength, 0.0, 1.2)) * 0.58
    out = rgb.copy()
    lum = _lh_luminance(out)
    mid = np.clip(1.0 - np.abs(lum - 0.44) / 0.38, 0.0, 1.0) ** 1.1
    mid = cv2.GaussianBlur(mid, (0, 0), 4.0)

    inst = _cinema_fields_or_build(h, w, seed, eps, cinema_fields)
    breath = cv2.GaussianBlur(
        _render_rng(seed, 40).normal(0, 1, (h, w)).astype(np.float32), (0, 0), max(24.0, min(h, w) / 14.0)
    )
    breath = np.clip(0.5 + 0.5 * breath, 0.0, 1.0)

    chroma_lf = cv2.GaussianBlur(
        _render_rng(seed, 41).normal(0, 1, (h, w)).astype(np.float32), (0, 0), max(16.0, min(h, w) / 22.0)
    )
    out[:, :, 0] += chroma_lf * (0.0038 * s) * mid
    out[:, :, 2] -= chroma_lf * (0.0032 * s) * mid
    out[:, :, 1] += inst["drift"] * (0.0018 * s) * mid

    if blur_artifacts:
        ghost = cv2.GaussianBlur(out, (0, 0), max(2.2, min(h, w) / 280.0))
        ghost_w = (0.015 * s) * mid * (0.45 + 0.55 * np.abs(inst["drift"]))
        out = np.clip(out * (1.0 - ghost_w[:, :, None]) + ghost * ghost_w[:, :, None], 0.0, 1.0)

        lag = out.copy()
        lag[:, 1:, 0] = out[:, :-1, 0]
        lag[:, :-1, 2] = out[:, 1:, 2]
        lag_w = (0.010 * s) * mid * np.clip(0.35 + inst["edge_smoke"], 0.0, 1.0)
        out = np.clip(out * (1.0 - lag_w[:, :, None]) + lag * lag_w[:, :, None], 0.0, 1.0)

    gray = out.mean(axis=2, keepdims=True)
    sat = 1.0 + (breath - 0.5) * (0.038 * s) * mid
    out = np.clip(gray + (out - gray) * sat[:, :, None], 0.0, 1.0)
    luma_pulse = 1.0 + (breath - 0.5) * (0.018 * s) * mid
    out = np.clip(out * luma_pulse[:, :, None], 0.0, 1.0)

    density = 1.0 + inst["diffusion"] * (0.028 * s) * mid + inst["drift"] * (0.012 * s) * (1.0 - mid)
    density = cv2.GaussianBlur(density.astype(np.float32), (0, 0), 7.0)
    out = np.clip(out * density[:, :, None], 0.0, 1.0)

    edge = _lh_edge_strength_map(lum, eps)
    hi = np.power(np.clip((lum - 0.48) / 0.52, 0.0, 1.0), 1.05)
    bloom_edge = edge * hi * inst["edge_smoke"] * inst["diffusion"]
    edge_rgb = bloom_edge[:, :, None] * np.array([0.014, 0.011, 0.009], dtype=np.float32) * s
    out = np.clip(out + edge_rgb * (1.0 - out), 0.0, 1.0)

    dist = _radial_dist_norm(h, w, eps)
    frame_wobble = 1.0 - np.power(dist, 1.25) * (0.010 * s) * (0.5 + 0.5 * breath)
    y_i, x_i = np.indices((h, w), dtype=np.float32)
    yn = np.clip(y_i / max(h - 1, 1), 0.0, 1.0)
    xn = np.clip(x_i / max(w - 1, 1), 0.0, 1.0)
    corner = np.clip(yn * xn * (1.0 - yn) * (1.0 - xn) * 16.0, 0.0, 1.0)
    corner = np.clip(corner, 0.0, 1.0) ** 0.85
    out[:, :, 0] += corner * (0.0028 * s) * (breath - 0.5)
    out[:, :, 2] -= corner * (0.0024 * s) * (breath - 0.5)
    out = np.clip(out * frame_wobble[:, :, None], 0.0, 1.0)

    return np.clip(out, 0.0, 1.0)


def _apply_cinematic_light_sculpt(
    rgb: np.ndarray,
    subject_soft: np.ndarray,
    h: int,
    w: int,
    strength: float = 1.0,
    eps: float = 1e-6,
) -> np.ndarray:
    """
    Post-grade cinematic sculpt: face dodge, subject/BG separation, rim, soft local depth.
    No HDR/clarity — luminance-first, subject-weighted.
    """
    out = rgb.copy()
    lum = _lh_luminance(out)
    s = float(np.clip(strength, 0.0, 1.25))

    gx = cv2.Sobel(lum, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(lum, cv2.CV_32F, 0, 1, ksize=3)
    grad = np.sqrt(gx * gx + gy * gy)
    grad_norm = grad / (np.max(grad) + eps)

    blur_f = cv2.GaussianBlur(lum, (0, 0), 4.5)
    blur_w = cv2.GaussianBlur(lum, (0, 0), 14.0)
    direction = np.clip(lum - blur_f, 0.0, 1.0)
    edge_contrast = np.clip(np.abs(lum - blur_w), 0.0, 1.0)

    sub = cv2.GaussianBlur(np.clip(subject_soft, 0.0, 1.0), (0, 0), 8)

    # Face dodge (bone structure, not HDR)
    face_lum = np.clip(1.0 - np.abs(lum - 0.44) / 0.30, 0.0, 1.0) ** 1.1
    skin = np.clip(1.0 - np.abs(out[:, :, 0] - out[:, :, 1]) / 0.13, 0.0, 1.0)
    skin *= np.clip(1.0 - np.abs(lum - 0.36) / 0.34, 0.0, 1.0)
    bone = np.clip(grad_norm * 0.55 + edge_contrast * 0.45, 0.0, 1.0)
    dodge = sub * face_lum * (0.45 + 0.55 * skin) * bone
    dodge *= 1.0 - np.power(np.clip((lum - 0.72) / 0.28, 0.0, 1.0), 1.2)
    dodge = cv2.GaussianBlur(dodge, (0, 0), 5)
    lift = 0.024 * s * dodge * (1.0 - lum)
    out = np.clip(out + lift[:, :, None], 0.0, 1.0)
    lum = _lh_luminance(out)

    # Subject-aware background compression
    inv = np.clip(1.0 - sub, 0.0, 1.0)
    inv_wide = cv2.GaussianBlur(inv, (0, 0), max(16.0, min(h, w) / 28.0))
    ring = np.clip(inv_wide * 1.15 - sub * 0.85, 0.0, 1.0) ** 1.05
    ring = cv2.GaussianBlur(ring, (0, 0), 6)

    gray = out.mean(axis=2, keepdims=True)
    bg_compress = inv_wide * (1.0 - np.clip(lum / 0.85, 0.0, 1.0))
    chroma = out - gray
    out = gray + chroma * (1.0 - 0.07 * s * bg_compress[:, :, None])
    out = np.clip(out * (1.0 - 0.028 * s * ring[:, :, None]), 0.0, 1.0)

    # Rim emphasis (backlight / shoulder / contour)
    rim = (
        np.power(grad_norm, 0.82)
        * np.power(direction, 0.9)
        * (0.30 + 0.70 * inv_wide)
        * (0.40 + 0.60 * sub)
    )
    rim = np.clip(rim, 0.0, 1.0)
    rim = cv2.GaussianBlur(rim, (0, 0), 2.5)
    rim_rgb = rim[:, :, None] * np.array([0.038, 0.034, 0.030], dtype=np.float32) * (0.5 * s)
    out = np.clip(out + rim_rgb * (1.0 - out), 0.0, 1.0)

    # Local contrast sculpt (dimensionality, not digital clarity)
    lum = _lh_luminance(out)
    wide = cv2.GaussianBlur(out, (0, 0), 5.0)
    sculpt_zone = sub * np.clip(1.0 - np.abs(lum - 0.46) / 0.36, 0.0, 1.0)
    sculpt_zone *= 1.0 - 0.55 * np.clip((grad_norm - 0.42) / 0.38, 0.0, 1.0)
    sculpt_zone = cv2.GaussianBlur(sculpt_zone, (0, 0), 5)

    micro = out - wide
    out = np.clip(out + micro * sculpt_zone[:, :, None] * (0.20 * s), 0.0, 1.0)

    return np.clip(out, 0.0, 1.0)


def _apply_subject_acutance_guard(
    rgb: np.ndarray,
    subject_soft: np.ndarray,
    h: int,
    w: int,
    amount: float = 0.11,
) -> np.ndarray:
    """Restore edge acutance on performer zone after atmospheric passes (not global sharpen)."""
    guard = cv2.GaussianBlur(np.clip(subject_soft, 0.0, 1.0), (0, 0), max(5.0, min(h, w) / 80.0))
    sig_fine = max(0.85, min(h, w) / 520.0)
    sig_mid = max(1.6, min(h, w) / 320.0)
    blur_f = cv2.GaussianBlur(rgb, (0, 0), sig_fine)
    blur_m = cv2.GaussianBlur(rgb, (0, 0), sig_mid)
    detail_f = rgb - blur_f
    detail_m = rgb - blur_m
    out = np.clip(rgb + detail_f * guard[:, :, None] * amount, 0.0, 1.0)
    out = np.clip(out + detail_m * guard[:, :, None] * (amount * 0.42), 0.0, 1.0)
    lum = _lh_luminance(rgb)
    edge = _lh_edge_strength_map(lum)
    edge_boost = np.clip(edge * (1.0 - guard * 0.35), 0.0, 1.0)
    out = np.clip(out + detail_f * edge_boost[:, :, None] * (amount * 0.22), 0.0, 1.0)
    return out


def _radial_dist_norm(h: int, w: int, eps: float = 1e-6) -> np.ndarray:
    y, x = np.indices((h, w), dtype=np.float32)
    cx, cy = w / 2, h / 2
    dx = (x - cx) / cx
    dy = (y - cy) / cy
    dist = np.sqrt(dx * dx + dy * dy)
    return dist / (np.max(dist) + eps)


def _optical_ui_to_unit(value: float) -> float:
    """Map UI slider 0–100 (or legacy 0–1) to internal strength (aggressive curve)."""
    v = float(value)
    if v > 1.0:
        v = v / 100.0
    v = float(np.clip(v, 0.0, 1.0))
    # Ease-in: mid slider values land much harder than linear (100 still = full).
    return float(np.clip(np.power(v, 0.48), 0.0, 1.0))


def _lh_directional_blur_rgb(
    rgb: np.ndarray,
    angle_deg: float,
    kernel_len: int,
    eps: float = 1e-6,
) -> np.ndarray:
    """Separable-ish motion kernel along ``angle_deg`` (degrees)."""
    ksize = max(3, int(kernel_len) | 1)
    k = np.zeros((ksize, ksize), dtype=np.float32)
    cx = cy = ksize // 2
    theta = np.deg2rad(float(angle_deg))
    dx = float(np.cos(theta))
    dy = float(np.sin(theta))
    for i in range(ksize):
        t = i - cx
        xi = int(round(cx + t * dx))
        yi = int(round(cy + t * dy))
        if 0 <= xi < ksize and 0 <= yi < ksize:
            k[yi, xi] = 1.0
    k_sum = float(np.sum(k))
    if k_sum <= eps:
        k[cy, cx] = 1.0
        k_sum = 1.0
    k /= k_sum
    out = np.empty_like(rgb)
    for c in range(3):
        out[:, :, c] = cv2.filter2D(rgb[:, :, c], -1, k)
    return out


def _lh_motion_flow_apply(
    rgb: np.ndarray,
    flow_strength: float,
    flow_angle_deg: float = -15.0,
    subject_soft: np.ndarray | None = None,
    seed: int | None = DEFAULT_RENDER_SEED,
    eps: float = 1e-6,
) -> np.ndarray:
    """
    Directional highlight smear — slow-shutter livehouse light trails without losing subject.
    ``flow_strength`` is 0–1 (map UI 0–100 via :func:`_optical_ui_to_unit`).
    """
    s = float(np.clip(flow_strength, 0.0, 1.0))
    if s <= 1e-6:
        return rgb
    h, w = rgb.shape[:2]
    lum = _lh_luminance(rgb)
    if subject_soft is None:
        subject_soft, _, _ = _subject_readability_masks(rgb, lum, h, w, eps)
    edge = _lh_edge_strength_map(lum, eps)

    streak_mask = np.power(np.clip((lum - 0.48) / 0.38, 0.0, 1.0), 1.15)
    streak_mask *= np.clip(1.0 - subject_soft * 0.55, 0.0, 1.0)
    streak_mask = cv2.GaussianBlur(streak_mask.astype(np.float32), (0, 0), 2.8)

    # Optional flow hint from downscaled Farneback on luminance highlights.
    angle = float(flow_angle_deg)
    if s >= 0.12 and min(h, w) >= 64:
        small_h = max(32, h // 4)
        small_w = max(32, w // 4)
        lum_small = cv2.resize(lum, (small_w, small_h), interpolation=cv2.INTER_AREA)
        lum_u8 = np.clip(lum_small * 255.0, 0, 255).astype(np.uint8)
        flow = cv2.calcOpticalFlowFarneback(
            lum_u8,
            lum_u8,
            None,
            0.5,
            3,
            15,
            3,
            5,
            1.2,
            0,
        )
        mag = np.sqrt(flow[:, :, 0] ** 2 + flow[:, :, 1] ** 2)
        hi_small = np.clip((lum_small - 0.58) / 0.34, 0.0, 1.0)
        if float(np.max(mag)) > eps and float(np.sum(hi_small > 0.2)) > 8:
            top = mag > np.percentile(mag[hi_small > 0.15], 85) if np.any(hi_small > 0.15) else mag > 0
            if np.any(top):
                mean_vx = float(np.mean(flow[:, :, 0][top]))
                mean_vy = float(np.mean(flow[:, :, 1][top]))
                if abs(mean_vx) + abs(mean_vy) > eps:
                    angle = float(np.degrees(np.arctan2(mean_vy, mean_vx)))

    kernel_len = max(5, int(round(5 + s * 0.52 * min(h, w) / 100)))
    blurred = _lh_directional_blur_rgb(rgb, angle, kernel_len, eps)
    alpha = streak_mask * (1.0 - edge * 0.38) * (0.24 + 1.12 * s)
    alpha = np.clip(alpha, 0.0, 0.90)
    alpha3 = alpha[:, :, None]
    return np.clip(rgb * (1.0 - alpha3) + blurred * alpha3, 0.0, 1.0)


def _lh_analog_wear_apply(
    rgb: np.ndarray,
    wear_strength: float,
    seed: int | None = DEFAULT_RENDER_SEED,
    eps: float = 1e-6,
) -> np.ndarray:
    """
    Subtle VHS / scan / gate-weave imperfections — memory medium, not cheap FX.
    ``wear_strength`` is 0–1.
    """
    s = float(np.clip(wear_strength, 0.0, 1.0))
    if s <= 1e-6:
        return rgb
    h, w = rgb.shape[:2]
    lum = _lh_luminance(rgb)
    edge = _lh_edge_strength_map(lum, eps)
    out = rgb.copy()

    # Edge-weighted chromatic aberration (sub-pixel shifts).
    ca_px = 0.65 + 1.45 * s
    shift_r = max(0, int(round(ca_px * (1.0 + 0.55 * s))))
    shift_b = max(0, int(round(ca_px * (0.75 + 0.45 * s))))
    if shift_r > 0 or shift_b > 0:
        edge_w = np.clip(edge * 1.2, 0.0, 1.0)
        mix = (0.16 + 0.72 * s) * edge_w
        if shift_r > 0:
            r_shift = np.roll(out[:, :, 0], shift_r, axis=1)
            out[:, :, 0] = out[:, :, 0] * (1.0 - mix) + r_shift * mix
        if shift_b > 0:
            b_shift = np.roll(out[:, :, 2], -shift_b, axis=1)
            out[:, :, 2] = out[:, :, 2] * (1.0 - mix) + b_shift * mix

    # Gate weave — per-row horizontal wobble.
    rng = _render_rng(seed, 40)
    freq = 0.16 + 0.14 * s
    phase = float(rng.uniform(0.0, 2.0 * np.pi))
    y_idx = np.arange(h, dtype=np.float32)
    row_shift = np.round(np.sin(y_idx * freq + phase) * (0.35 + 0.85 * s)).astype(np.int32)
    woven = np.empty_like(out)
    for yi in range(h):
        woven[yi] = np.roll(out[yi], int(row_shift[yi]), axis=0)
    weave_mix = 0.08 + 0.48 * s
    out = out * (1.0 - weave_mix) + woven * weave_mix

    # Exposure pump — very low-frequency gain drift.
    ch, cw = max(4, h // 36), max(4, w // 36)
    flicker_field = _render_rng(seed, 41).normal(0.0, 1.0, (ch, cw)).astype(np.float32)
    flicker = cv2.resize(flicker_field, (w, h), interpolation=cv2.INTER_LINEAR)
    flicker = cv2.GaussianBlur(flicker, (0, 0), max(8.0, min(h, w) / 38.0))
    gain = 1.0 + flicker * (0.028 + 0.088 * s)
    out = np.clip(out * gain[:, :, None], 0.0, 1.0)

    # Irregular vignette + shadow dust.
    dist = _radial_dist_norm(h, w, eps)
    inst = _memory_cinema_fields(h, w, seed, eps)
    vig = np.power(dist, 1.25 + 0.55 * s) * (0.82 + 0.18 * inst["haze_patch"])
    vig = np.clip(1.0 - vig * (0.14 + 0.50 * s), 0.62, 1.0)
    out = np.clip(out * vig[:, :, None], 0.0, 1.0)

    shadow_w = np.clip((0.38 - lum) / 0.38, 0.0, 1.0) ** 1.08
    dust = cv2.GaussianBlur(
        _render_rng(seed, 42).normal(0.0, 1.0, (h, w)).astype(np.float32),
        (0, 0),
        max(5.0, min(h, w) / 58.0),
    )
    out += (
        dust[:, :, None]
        * shadow_w[:, :, None]
        * np.array([-0.012, -0.005, 0.009], dtype=np.float32)[None, None, :]
        * s
        * 1.35
    )
    return np.clip(out, 0.0, 1.0)


def _lh_night_lift_apply(
    rgb: np.ndarray,
    night_strength: float,
    eps: float = 1e-6,
) -> np.ndarray:
    """Lifted blacks + cool shadow air (filmic toe)."""
    s = _optical_ui_to_unit(night_strength)
    if s <= 1e-6:
        return rgb
    lum = _lh_luminance(rgb)
    toe = np.clip((0.30 - lum) / 0.30, 0.0, 1.0) ** 0.95
    lift = toe[:, :, None] * (0.024 + 0.062 * s) * np.array([0.78, 0.92, 1.14], dtype=np.float32)
    out = rgb + lift - rgb * toe[:, :, None] * (0.48 * s)
    shadow = np.clip((0.42 - lum) / 0.42, 0.0, 1.0) ** 1.0
    tint = np.array([0.96, 0.94, 1.10], dtype=np.float32)
    out = out * (1.0 - shadow[:, :, None] * 0.36 * s) + shadow[:, :, None] * tint * (0.36 * s)
    return np.clip(out, 0.0, 1.0)


def _lh_dream_soften_apply(
    rgb: np.ndarray,
    dream_strength: float,
    subject_soft: np.ndarray | None = None,
    eps: float = 1e-6,
) -> np.ndarray:
    """Local contrast reduction + edge-aware softening (not beauty filter)."""
    s = _optical_ui_to_unit(dream_strength)
    if s <= 1e-6:
        return rgb
    h, w = rgb.shape[:2]
    lum = _lh_luminance(rgb)
    if subject_soft is None:
        subject_soft, _, _ = _subject_readability_masks(rgb, lum, h, w, eps)
    blur_l = cv2.GaussianBlur(lum, (0, 0), 12.0)
    local = lum - blur_l
    lum_soft = blur_l + local * (1.0 - 0.88 * s)
    scale = lum_soft / (lum + eps)
    out = np.clip(rgb * scale[:, :, None], 0.0, 1.0)
    edge = _lh_edge_strength_map(lum, eps)
    soft_mask = np.clip((edge - 0.04) / 0.24, 0.0, 1.0) * (1.0 - subject_soft * 0.32)
    blurred = cv2.GaussianBlur(out, (0, 0), 3.5 + 7.0 * s)
    mix = np.clip(soft_mask * (0.22 + 0.78 * s), 0.0, 0.75)[:, :, None]
    return np.clip(out * (1.0 - mix) + blurred * mix, 0.0, 1.0)


def apply_optical_console_enhancements(
    img_rgb: np.ndarray,
    *,
    air: float = 0.0,
    halation: float = 0.0,
    night: float = 0.0,
    dream: float = 0.0,
    flow: float = 0.0,
    time: float = 0.0,
    wear: float = 0.0,
    flow_angle: float = -15.0,
    skip_bloom: bool = False,
    skip_halation: bool = False,
    skip_grain: bool = False,
    seed: int | None = DEFAULT_RENDER_SEED,
) -> np.ndarray:
    """
    Full optical console post stack (UI 0–100).

    ``skip_*`` avoids double-applying when variant kernel already baked sliders in.
    """
    if not any(
        _optical_ui_to_unit(v) > 1e-6
        for v in (air, halation, night, dream, flow, time, wear)
    ):
        return img_rgb

    img = img_rgb.astype(np.float32) / 255.0
    h, w = img.shape[:2]
    lum = _lh_luminance(img)
    subject_soft, bloom_damp, _ = _subject_readability_masks(img, lum, h, w)

    air_s = _optical_ui_to_unit(air)
    if air_s > 1e-6 and not skip_bloom:
        img, _ = _lh_multiscale_bloom_apply(
            img,
            h,
            w,
            bloom_strength=0.92 + 3.15 * air_s,
            bloom_damp=bloom_damp,
            subject_soft=subject_soft,
            seed=seed,
        )
        lum = _lh_luminance(img)

    hal_s = _optical_ui_to_unit(halation)
    if hal_s > 1e-6 and not skip_halation:
        img = _film_halation_apply(
            img,
            lum,
            0.92 + 2.35 * hal_s,
            light_rim_hint=None,
            subject_soft=subject_soft,
            seed=seed,
        )
        lum = _lh_luminance(img)

    night_s = _optical_ui_to_unit(night)
    if night_s > 1e-6:
        img = _lh_night_lift_apply(img, night)
        lum = _lh_luminance(img)

    dream_s = _optical_ui_to_unit(dream)
    if dream_s > 1e-6:
        img = _lh_dream_soften_apply(img, dream, subject_soft=subject_soft)

    time_s = _optical_ui_to_unit(time)
    if time_s > 1e-6 and not skip_grain:
        lum = _lh_luminance(img)
        shadows, _, _ = _lh_tone_masks(lum)
        img = _film_grain_apply(
            img,
            lum,
            0.68 + 2.35 * time_s,
            h,
            w,
            shadows=shadows,
            subject_soft=subject_soft,
            seed=seed,
        )

    flow_s = _optical_ui_to_unit(flow)
    if flow_s > 1e-6:
        img = _lh_motion_flow_apply(
            img,
            flow_s,
            flow_angle_deg=flow_angle,
            subject_soft=subject_soft,
            seed=seed,
        )

    wear_s = _optical_ui_to_unit(wear)
    if wear_s > 1e-6:
        img = _lh_analog_wear_apply(img, wear_s, seed=seed)

    return (np.clip(img, 0.0, 1.0) * 255.0).astype(np.uint8)


def apply_optical_p1_enhancements(
    img_rgb: np.ndarray,
    *,
    flow: float = 0.0,
    wear: float = 0.0,
    flow_angle: float = -15.0,
    seed: int | None = DEFAULT_RENDER_SEED,
) -> np.ndarray:
    """
    P1 post-grade optical stack: **声波** (motion flow) then **磨损** (analog wear).

    ``flow`` / ``wear`` accept UI scale 0–100 or internal 0–1.
    """
    return apply_optical_console_enhancements(
        img_rgb,
        flow=flow,
        wear=wear,
        flow_angle=flow_angle,
        seed=seed,
    )


def _film_core_render(
    img: np.ndarray,
    h: int,
    w: int,
    *,
    bloom_strength: float,
    halation_strength: float,
    color_strength: float,
    grain_strength: float,
    seed: int | None = DEFAULT_RENDER_SEED,
    style: str = "copper_brown",
    shadow_finish: bool = True,
    eps: float = 1e-6,
) -> np.ndarray:
    """Emotional memory cinema: irregular air → grade → focus → grain → chromatic shadow → memory engine."""
    base = _apply_filmic_base(img, eps)
    lum_base = _lh_luminance(base)
    dist_norm = _radial_dist_norm(h, w, eps)
    subject_soft, bloom_damp, color_guard = _subject_readability_masks(
        base, lum_base, h, w, eps
    )
    cinema_fields = _memory_cinema_fields(h, w, seed, eps)
    energy, highlight_seed = _apply_bloom_stack(
        base,
        h,
        w,
        bloom_strength,
        bloom_damp=bloom_damp,
        subject_soft=subject_soft,
        seed=seed,
        cinema_fields=cinema_fields,
        eps=eps,
    )
    lum_pre = _lh_luminance(energy)
    energy = _apply_halation(
        energy,
        lum_pre,
        halation_strength,
        light_rim_hint=highlight_seed,
        subject_soft=subject_soft,
        seed=seed,
        eps=eps,
    )
    lum_haze = _lh_luminance(energy)
    energy = _lh_atmospheric_depth_apply(
        energy,
        lum_haze,
        h,
        w,
        subject_soft,
        dist_norm,
        strength=0.20,
        highlight_seed=highlight_seed,
        seed=seed,
        cinema_fields=cinema_fields,
        eps=eps,
    )
    energy = _apply_split_tone(energy, style, color_strength, color_guard, eps)
    energy = _lh_highlight_shoulder(energy, eps)
    energy = _apply_cinematic_light_sculpt(energy, subject_soft, h, w, strength=0.88, eps=eps)
    lum = _lh_luminance(energy)
    shadows, _, _ = _lh_tone_masks(lum, eps)
    energy = _apply_film_grain(
        energy,
        lum,
        grain_strength,
        h,
        w,
        shadows=shadows,
        subject_soft=subject_soft,
        seed=seed,
        eps=eps,
    )
    if shadow_finish:
        lum_fin = _lh_luminance(energy)
        shadows_fin, _, _ = _lh_tone_masks(lum_fin, eps)
        vig_guard = 1.0 - 0.35 * subject_soft * (1.0 - dist_norm)
        energy = _film_shadow_density_finish(
            energy,
            lum_fin,
            shadows_fin,
            dist_norm,
            seed=seed,
            cinema_fields=cinema_fields,
            chromatic_strength=0.58,
            eps=eps,
        )
        energy = np.clip(energy * vig_guard[:, :, None], 0.0, 1.0)
    lum_mem = _lh_luminance(energy)
    energy = _apply_emotional_memory_engine(
        energy,
        lum_mem,
        subject_soft,
        dist_norm,
        highlight_seed,
        h,
        w,
        seed=seed,
        strength=0.18,
        cinema_fields=cinema_fields,
        clarity_first=True,
        eps=eps,
    )
    energy = _apply_subject_acutance_guard(energy, subject_soft, h, w, amount=0.32)
    return np.clip(energy, 0.0, 1.0)


# =============================================================================
# Primary style entry — Livehouse film (product default)
# =============================================================================


def apply_livehouse_film(
    img_rgb: np.ndarray,
    bloom_strength: float = 2.0,
    halation_strength: float = 1.1,
    color_strength: float = 1.0,
    grain_strength: float = 1.0,
    seed: int | None = DEFAULT_RENDER_SEED,
) -> np.ndarray:
    """
    Livehouse movie-still render: Christopher Doyle–style stage light, practical haze,
    Vision3/CineStill analog grain, 90s HK emotional color — not an Instagram film filter.
    """

    img = img_rgb.astype(np.float32) / 255.0
    h, w = img.shape[:2]
    energy = _film_core_render(
        img,
        h,
        w,
        bloom_strength=bloom_strength,
        halation_strength=halation_strength,
        color_strength=color_strength,
        grain_strength=grain_strength,
        seed=seed,
        style="copper_brown",
        shadow_finish=True,
    )
    return (energy * 255).astype(np.uint8)


# =============================================================================
# Style preset variants (compose profile + optional extra atmosphere)
# =============================================================================


def apply_livehouse_cold_film_v2(
    img_rgb: np.ndarray,
    seed: int | None = DEFAULT_RENDER_SEED,
) -> np.ndarray:
    """Faded cream print look (variant ``film_cold_v2``): style profile + cream bloom/fog."""
    img = img_rgb.astype(np.float32) / 255.0
    h, w = img.shape[:2]
    eps = 1e-6

    # 1. 褪色曲线 + 轻抬黑
    img = img / (img + 0.48)
    img = np.clip(img * 0.94 + 0.034, 0, 1)

    lum = _lh_luminance(img)
    shadows, mids, highlights = _lh_tone_masks(lum, eps)

    # 2. Style profile: faded cream（中间调为主）
    img = _apply_split_tone(img, "faded_cream", color_strength=1.0, eps=eps)

    # 3. 高光滚染：奶油亮部（同色系，非对冲）
    hl = cv2.GaussianBlur(highlights, (0, 0), 12)
    cream_roll = np.array([1.02, 1.0, 0.97], dtype=np.float32)
    img = img * (1.0 - 0.24 * hl[:, :, None]) + cream_roll * (0.24 * hl[:, :, None])
    img = np.clip(img, 0, 1)

    lum2 = _lh_luminance(img)

    # 4. Bloom：环境奶油光（简化层数，统一 tint）
    bright = np.clip((lum2 - 0.32) / 0.68, 0, 1)
    bright = cv2.GaussianBlur(bright, (0, 0), 3)
    b1 = cv2.GaussianBlur(bright, (0, 0), 16)
    b2 = cv2.GaussianBlur(bright, (0, 0), 42)
    bloom = np.clip(b1 * 0.55 + b2 * 0.45, 0, 0.55)
    cream_bloom = np.stack([bloom * 0.98, bloom * 0.94, bloom * 0.88], axis=2)
    img = np.clip(img + cream_bloom * 0.38, 0, 1)

    # 5. 轻雾（同奶油向量，避免二次偏色）
    fog = cv2.GaussianBlur(img, (0, 0), 22)
    fog_tint = fog * np.array([1.01, 0.99, 0.96], dtype=np.float32)
    img = np.clip(img * 0.88 + fog_tint * 0.12, 0, 1)

    lum3 = _lh_luminance(img)
    img = _apply_film_grain(img, lum3, 0.85, h, w, shadows=shadows, seed=seed, eps=eps)

    # 10. 暗角
    y, x = np.indices((h, w), dtype=np.float32)
    cx, cy = w / 2, h / 2
    dist = np.sqrt((x - cx) ** 2 + (y - cy) ** 2)
    dist /= dist.max() + eps
    vignette = 1 - np.power(dist, 1.32) * 0.21
    img *= vignette[:, :, None]

    img = np.clip(img, 0, 1)
    return (img * 255).astype(np.uint8)


def apply_subject_lift(img: np.ndarray, strength: float = 0.25) -> np.ndarray:
    h, w = img.shape[:2]
    eps = 1e-6

    # ---------------------------
    # 1. 亮度
    # ---------------------------
    lum = 0.2126*img[:,:,0] + 0.7152*img[:,:,1] + 0.0722*img[:,:,2]

    # ---------------------------
    # 2. 梯度（找结构）
    # ---------------------------
    gx = cv2.Sobel(lum, cv2.CV_32F, 1, 0)
    gy = cv2.Sobel(lum, cv2.CV_32F, 0, 1)
    grad = np.sqrt(gx*gx + gy*gy)
    grad = grad / (np.max(grad) + eps)

    # ---------------------------
    # 3. 中心权重（摄影偏好）
    # ---------------------------
    y, x = np.indices((h, w))
    cx, cy = w/2, h/2

    dist = np.sqrt((x-cx)**2 + (y-cy)**2)
    dist /= dist.max()

    center_weight = 1.0 - dist**1.5

    # ---------------------------
    # 4. 主体mask
    # ---------------------------
    subject_mask = (
        0.5 * lum +
        0.3 * grad +
        0.2 * center_weight
    )

    subject_mask = subject_mask / (np.max(subject_mask) + eps)
    subject_mask = np.clip(subject_mask, 0, 1)

    # 平滑一下（关键）
    subject_mask = cv2.GaussianBlur(subject_mask, (0,0), 15)

    # ---------------------------
    # 5. 提亮（只作用在主体）
    # ---------------------------
    lift = strength * subject_mask

    img = img + lift[:,:,None]

    return np.clip(img, 0, 1)


def apply_livehouse_cold_film_v3(img_rgb: np.ndarray) -> np.ndarray:
    img = img_rgb.astype(np.float32) / 255.0
    h, w = img.shape[:2]
    eps = 1e-6

    # ---------------------------
    # 1. 更温和的 tone curve（关键）
    # ---------------------------
    img = img / (img + 0.5)   # 原来 0.8 → 改小（更亮）

    # 提黑（但更轻）
    img = img * 0.95 + 0.05

    # ---------------------------
    # 2. Luminance
    # ---------------------------
    lum = 0.2126*img[:,:,0] + 0.7152*img[:,:,1] + 0.0722*img[:,:,2]

    # ---------------------------
    # 3. 冷色调（收敛版）
    # ---------------------------
    shadows = np.clip((0.5 - lum)/0.5, 0, 1)
    highlights = np.clip((lum - 0.6)/0.4, 0, 1)

    img[:,:,2] += 0.08 * shadows
    img[:,:,1] += 0.02 * shadows
    img[:,:,0] -= 0.03 * shadows

    # 高光轻微偏冷（不要太多）
    img[:,:,2] += 0.03 * highlights

    # ---------------------------
    # 4. 去饱和（但保留一点）
    # ---------------------------
    gray = img.mean(axis=2, keepdims=True)
    img = gray + (img - gray) * 0.75   # 原来 0.65 → 提一点

    # ---------------------------
    # 5. Bloom（加强一点点）
    # ---------------------------
    mask = np.clip((lum - 0.65)/0.35, 0, 1)

    bloom = cv2.GaussianBlur(mask, (0,0), 10)
    img += bloom[:,:,None] * 0.35   # 原来 0.25 → 提亮光源

    # ---------------------------
    # 6. Fog（减弱！！！）
    # ---------------------------
    fog = cv2.GaussianBlur(img, (0,0), 18)

    # 关键：只在高光区域加雾
    fog_mask = np.clip((lum - 0.5)/0.5, 0, 1)

    img = img * (1 - 0.08 * fog_mask[:,:,None]) + fog * (0.08 * fog_mask[:,:,None])

    # ---------------------------
    # 7. 颗粒（更真实）
    # ---------------------------
    grain = _render_rng(DEFAULT_RENDER_SEED, 50).normal(0, 0.012, (h,w,3)).astype(np.float32)

    grain_mask = np.clip(1 - np.abs(lum - 0.5)*2, 0,1)
    img += grain * grain_mask[:,:,None]

    # ---------------------------
    # 8. 暗角（更轻）
    # ---------------------------
    y, x = np.indices((h, w))
    cx, cy = w/2, h/2
    dist = np.sqrt((x-cx)**2 + (y-cy)**2)
    dist /= dist.max()

    vignette = 1 - dist**1.3 * 0.3
    img *= vignette[:,:,None]

    # ---------------------------
    # 9. 高光恢复（关键！！！）
    # ---------------------------
    img += highlights[:,:,None] * 0.08

    # ---------------------------
    # Final
    # ---------------------------
    img = np.clip(img, 0, 1)
    img = apply_subject_lift(img, strength=0.5)
    return (img * 255).astype(np.uint8)


def apply_livehouse_cold_film_v3(img_rgb: np.ndarray) -> np.ndarray:
    img = img_rgb.astype(np.float32) / 255.0
    h, w = img.shape[:2]
    eps = 1e-6

    # ---------------------------
    # 1. Tone Curve（更通透）
    # ---------------------------
    img = img / (img + 0.5)
    img = img * 0.96 + 0.04

    # ---------------------------
    # 2. Luminance
    # ---------------------------
    lum = 0.2126*img[:,:,0] + 0.7152*img[:,:,1] + 0.0722*img[:,:,2]

    shadows = np.clip((0.5 - lum)/0.5, 0, 1)
    highlights = np.clip((lum - 0.6)/0.4, 0, 1)

    # ---------------------------
    # 3. 冷色调（克制版）
    # ---------------------------
    img[:,:,2] += 0.08 * shadows
    img[:,:,1] += 0.02 * shadows
    img[:,:,0] -= 0.03 * shadows

    img[:,:,2] += 0.03 * highlights

    # ---------------------------
    # 4. 轻微去饱和
    # ---------------------------
    gray = img.mean(axis=2, keepdims=True)
    img = gray + (img - gray) * 0.75

    # ---------------------------
    # 5. Bloom（只增强光）
    # ---------------------------
    bright_mask = np.clip((lum - 0.65)/0.35, 0, 1)

    bloom = cv2.GaussianBlur(bright_mask, (0,0), 10)
    img += bloom[:,:,None] * 0.35

    # ---------------------------
    # 6. Halation（红色边缘）
    # ---------------------------
    edge = cv2.Sobel(lum, cv2.CV_32F, 1, 0)**2 + cv2.Sobel(lum, cv2.CV_32F, 0, 1)**2
    edge = np.sqrt(edge)
    edge = edge / (np.max(edge) + eps)

    halation_mask = edge * bright_mask
    halation = cv2.GaussianBlur(halation_mask, (0,0), 15)

    img[:,:,0] += halation * 0.12
    img[:,:,1] += halation * 0.02

    # ---------------------------
    # 7. 正确 Fog（只来自高光！！！）
    # ---------------------------
    fog_source = img * bright_mask[:,:,None]
    fog = cv2.GaussianBlur(fog_source, (0,0), 20)

    img += fog * 0.12

    # ---------------------------
    # 8. Clarity（去灰关键）
    # ---------------------------
    blur_small = cv2.GaussianBlur(img, (0,0), 3)
    img = img + (img - blur_small) * 0.15

    # ---------------------------
    # 9. 主体提亮（自动）
    # ---------------------------
    gx = cv2.Sobel(lum, cv2.CV_32F, 1, 0)
    gy = cv2.Sobel(lum, cv2.CV_32F, 0, 1)
    grad = np.sqrt(gx*gx + gy*gy)
    grad = grad / (np.max(grad) + eps)

    y, x = np.indices((h, w))
    cx, cy = w/2, h/2
    dist = np.sqrt((x-cx)**2 + (y-cy)**2)
    dist /= dist.max()

    center_weight = 1 - dist**1.5

    subject_mask = 0.5*lum + 0.3*grad + 0.2*center_weight
    subject_mask = subject_mask / (np.max(subject_mask) + eps)
    subject_mask = cv2.GaussianBlur(subject_mask, (0,0), 15)

    img += subject_mask[:,:,None] * 0.15

    # ---------------------------
    # 10. 胶片颗粒（更粗粝）
    # ---------------------------
    grain_luma = _render_rng(DEFAULT_RENDER_SEED, 51).normal(0, 0.015, (h,w,1))
    grain_color = _render_rng(DEFAULT_RENDER_SEED, 52).normal(0, 0.008, (h,w,3))

    grain = grain_luma + grain_color

    grain_mask = np.clip(1 - np.abs(lum - 0.5)*2, 0,1)
    img += grain * grain_mask[:,:,None]

    # ---------------------------
    # 11. 暗角（轻）
    # ---------------------------
    vignette = 1 - dist**1.4 * 0.35
    img *= vignette[:,:,None]

    # ---------------------------
    # Final
    # ---------------------------
    img = np.clip(img, 0, 1)
    return (img * 255).astype(np.uint8)

def apply_livehouse_cold_film_v4(img_rgb: np.ndarray) -> np.ndarray:
    """Theatrical teal/warm split with filmic contrast; grain/halation toned down vs prior (less muddy).

    API id ``film_cold_v4`` / Lab label ``Film · Cinema``.
    """
    img = img_rgb.astype(np.float32) / 255.0
    h, w = img.shape[:2]
    eps = 1e-6

    # 1. Filmic base（略压整体明度，少「灰白浮」）
    img = img / (img + 0.44)
    img = np.clip(img * 0.968 + 0.022, 0, 1)

    lum = 0.2126 * img[:, :, 0] + 0.7152 * img[:, :, 1] + 0.0722 * img[:, :, 2]

    shadows = np.clip((0.52 - lum) / 0.52, 0, 1)
    shadows = cv2.GaussianBlur(shadows, (0, 0), 4)
    highlights = np.clip((lum - 0.60) / 0.40, 0, 1)
    highlights = cv2.GaussianBlur(highlights, (0, 0), 6)

    # 2. 分裂互补：阴影青、高光暖（略加强影院分离感）
    img[:, :, 2] += 0.052 * shadows
    img[:, :, 1] -= 0.012 * shadows
    img[:, :, 0] -= 0.020 * shadows

    img[:, :, 0] += 0.038 * highlights
    img[:, :, 1] += 0.018 * highlights
    img[:, :, 2] -= 0.024 * highlights

    img = np.clip(img, 0, 1)

    # 3. 色彩密度（略收彩，避免发灰「脏」）
    gray = img.mean(axis=2, keepdims=True)
    img = gray + (img - gray) * 0.88

    img = np.clip(img, 0, 1)

    lum2 = 0.2126 * img[:, :, 0] + 0.7152 * img[:, :, 1] + 0.0722 * img[:, :, 2]

    # 4. 中调微对比（克制，少颗粒感硬边）
    blur_m = cv2.GaussianBlur(img, (0, 0), 2.5)
    mid_w = np.clip(1.0 - np.abs(lum2 - 0.5) * 2.4, 0, 1)
    img = img + (img - blur_m) * (0.09 * mid_w[:, :, None])

    img = np.clip(img, 0, 1)

    # 5. 高光 bloom（略扩一点暖空气光）
    spec = np.clip((lum2 - 0.68) / 0.32, 0, 1)
    spec = cv2.GaussianBlur(spec, (0, 0), 8)
    b_small = cv2.GaussianBlur(spec, (0, 0), 11)
    b_big = cv2.GaussianBlur(spec, (0, 0), 26)
    bloom = b_small * 0.48 + b_big * 0.32
    warm_b = np.stack([bloom * 1.0, bloom * 0.78, bloom * 0.58], axis=2)
    img = img + warm_b * 0.12

    img = np.clip(img, 0, 1)

    lum3 = 0.2126 * img[:, :, 0] + 0.7152 * img[:, :, 1] + 0.0722 * img[:, :, 2]

    # 6. halation（轻红晕，避免脏边）
    gx = cv2.Sobel(lum3, cv2.CV_32F, 1, 0)
    gy = cv2.Sobel(lum3, cv2.CV_32F, 0, 1)
    edge = np.sqrt(gx * gx + gy * gy)
    edge = edge / (np.max(edge) + eps)
    spec2 = np.clip((lum3 - 0.66) / 0.34, 0, 1)
    spec2 = cv2.GaussianBlur(spec2, (0, 0), 5)
    hal = cv2.GaussianBlur(edge * spec2, (0, 0), 11)
    img[:, :, 0] += hal * 0.038
    img[:, :, 1] += hal * 0.010

    img = np.clip(img, 0, 1)

    lum4 = 0.2126 * img[:, :, 0] + 0.7152 * img[:, :, 1] + 0.0722 * img[:, :, 2]

    # 7. 颗粒：可见胶片感但不过「脏」（降强 + 少锐化颗粒）
    grain_l = _render_rng(DEFAULT_RENDER_SEED, 53).normal(0, 0.014, (h, w, 1)).astype(np.float32)
    grain_coarse = cv2.GaussianBlur(
        _render_rng(DEFAULT_RENDER_SEED, 54).normal(0, 1.0, (h, w, 1)).astype(np.float32),
        (0, 0),
        2.2,
    )
    if grain_coarse.ndim == 2:
        grain_coarse = grain_coarse[:, :, np.newaxis]
    grain_c = _render_rng(DEFAULT_RENDER_SEED, 55).normal(0, 0.006, (h, w, 3)).astype(np.float32)
    grain = grain_l * 0.78 + grain_coarse * 0.028 + grain_c * 0.9
    grain = cv2.GaussianBlur(grain, (0, 0), 0.65)
    gmask = np.clip(1.0 - np.abs(lum4 - 0.5) * 1.62, 0, 1)
    gmask = np.clip(gmask + np.clip(1.0 - lum4, 0, 1) * 0.22, 0, 1)
    img += grain * gmask[:, :, None] * 0.92

    grain_pop = img - cv2.GaussianBlur(img, (0, 0), 0.95)
    img = img + grain_pop * (0.018 * gmask[:, :, None])

    img = np.clip(img, 0, 1)

    # 8. 暗角（轻）
    y, x = np.indices((h, w), dtype=np.float32)
    cx, cy = w / 2, h / 2
    dist = np.sqrt((x - cx) ** 2 + (y - cy) ** 2)
    dist /= dist.max() + eps
    vignette = 1 - np.power(dist, 1.48) * 0.22
    img *= vignette[:, :, None]

    # 9. 整体略压亮度（幂曲线，主要收中高调发白）
    img = np.clip(np.power(np.maximum(img, 0.0), 1.02), 0, 1)

    img = np.clip(img, 0, 1)
    return (img * 255).astype(np.uint8)


def _film_emulation_grade(
    img_rgb: np.ndarray,
    *,
    filmic: float = 0.48,
    black_lift: float = 0.028,
    contrast: float = 1.06,
    saturation: float = 0.94,
    shadow_tint: tuple[float, float, float] = (0.0, 0.0, 0.0),
    highlight_tint: tuple[float, float, float] = (0.0, 0.0, 0.0),
    bloom: float = 0.0,
    halation: float = 0.0,
    clarity: float = 0.08,
    grain: float = 0.30,
    vignette: float = 0.14,
    seed: int | None = DEFAULT_RENDER_SEED,
    to_bw: bool = False,
    bw_mix: tuple[float, float, float] = (0.299, 0.587, 0.114),
) -> np.ndarray:
    """Shared pipeline for consumer/stock film emulations (RGB in/out 0–255 uint8)."""
    img = img_rgb.astype(np.float32) / 255.0
    h, w = img.shape[:2]
    eps = 1e-6

    img = img / (img + filmic)
    img = np.clip(img * (1.0 - black_lift * 0.5) + black_lift, 0, 1)

    lum = _lh_luminance(img)
    shadows = np.clip((0.50 - lum) / 0.50, 0, 1)
    shadows = cv2.GaussianBlur(shadows, (0, 0), 5)
    highlights = np.clip((lum - 0.58) / 0.42, 0, 1)
    highlights = cv2.GaussianBlur(highlights, (0, 0), 6)

    for c, d in enumerate(shadow_tint):
        img[:, :, c] += d * shadows
    for c, d in enumerate(highlight_tint):
        img[:, :, c] += d * highlights
    img = np.clip(img, 0, 1)

    gray = img.mean(axis=2, keepdims=True)
    img = gray + (img - gray) * saturation
    img = np.clip((img - 0.5) * contrast + 0.5, 0, 1)

    lum2 = _lh_luminance(img)
    if bloom > 0:
        bright = np.clip((lum2 - 0.62) / 0.38, 0, 1)
        bright = cv2.GaussianBlur(bright, (0, 0), 10)
        b = cv2.GaussianBlur(bright, (0, 0), 22)
        warm = np.stack([b * 1.0, b * 0.88, b * 0.72], axis=2)
        img = np.clip(img + warm * bloom, 0, 1)
        lum2 = _lh_luminance(img)

    if halation > 0:
        gx = cv2.Sobel(lum2, cv2.CV_32F, 1, 0)
        gy = cv2.Sobel(lum2, cv2.CV_32F, 0, 1)
        edge = np.sqrt(gx * gx + gy * gy)
        edge = edge / (np.max(edge) + eps)
        spec = np.clip((lum2 - 0.64) / 0.36, 0, 1)
        spec = cv2.GaussianBlur(spec, (0, 0), 5)
        hal = cv2.GaussianBlur(edge * spec, (0, 0), 12)
        img[:, :, 0] += hal * halation
        img[:, :, 1] += hal * (halation * 0.25)
        img = np.clip(img, 0, 1)
        lum2 = _lh_luminance(img)

    if clarity > 0:
        blur_s = cv2.GaussianBlur(img, (0, 0), 2.0)
        mid_w = np.clip(1.0 - np.abs(lum2 - 0.5) * 2.2, 0, 1)
        img = img + (img - blur_s) * (clarity * mid_w[:, :, None])
        img = np.clip(img, 0, 1)
        lum2 = _lh_luminance(img)

    if to_bw:
        lum_bw = (
            bw_mix[0] * img[:, :, 0] + bw_mix[1] * img[:, :, 1] + bw_mix[2] * img[:, :, 2]
        )
        img = np.stack([lum_bw, lum_bw, lum_bw], axis=2)
        img = np.clip((img - 0.5) * contrast + 0.5, 0, 1)
        lum2 = _lh_luminance(img)

    img = _apply_film_grain(img, lum2, grain, h, w, shadows=shadows, seed=seed, eps=eps)

    y, x = np.indices((h, w), dtype=np.float32)
    cx, cy = w / 2, h / 2
    dist = np.sqrt((x - cx) ** 2 + (y - cy) ** 2)
    dist /= dist.max() + eps
    img *= (1.0 - np.power(dist, 1.35) * vignette)[:, :, None]

    return (np.clip(img, 0, 1) * 255).astype(np.uint8)


def apply_film_portra_400(img_rgb: np.ndarray) -> np.ndarray:
    """Kodak Portra 400 — soft contrast, warm skin, gentle highlights."""
    return _film_emulation_grade(
        img_rgb,
        filmic=0.52,
        black_lift=0.038,
        contrast=1.04,
        saturation=0.90,
        shadow_tint=(0.02, 0.01, 0.04),
        highlight_tint=(0.05, 0.03, 0.01),
        bloom=0.10,
        clarity=0.06,
        grain=0.28,
        vignette=0.12,
        seed=61,
    )


def apply_film_gold_200(img_rgb: np.ndarray) -> np.ndarray:
    """Kodak Gold 200 — warm yellow-green consumer snapshot."""
    return _film_emulation_grade(
        img_rgb,
        filmic=0.46,
        contrast=1.10,
        saturation=1.05,
        shadow_tint=(-0.02, 0.03, 0.06),
        highlight_tint=(0.08, 0.06, 0.0),
        bloom=0.08,
        grain=0.34,
        vignette=0.16,
        seed=62,
    )


def apply_film_ektar_100(img_rgb: np.ndarray) -> np.ndarray:
    """Kodak Ektar 100 — vivid color, punchy contrast, clean neutrals."""
    return _film_emulation_grade(
        img_rgb,
        filmic=0.40,
        contrast=1.15,
        saturation=1.16,
        shadow_tint=(0.0, 0.02, 0.04),
        highlight_tint=(0.03, 0.02, 0.0),
        clarity=0.12,
        grain=0.22,
        vignette=0.10,
        seed=63,
    )


def apply_film_fuji_400h(img_rgb: np.ndarray) -> np.ndarray:
    """Fujifilm Pro 400H — soft pastels, cyan shadows, flattering skin."""
    return _film_emulation_grade(
        img_rgb,
        filmic=0.50,
        black_lift=0.04,
        contrast=1.02,
        saturation=0.88,
        shadow_tint=(0.04, 0.02, -0.02),
        highlight_tint=(0.02, 0.04, 0.03),
        bloom=0.06,
        clarity=0.05,
        grain=0.26,
        vignette=0.11,
        seed=64,
    )


def apply_film_fuji_classic_neg(img_rgb: np.ndarray) -> np.ndarray:
    """Fujifilm Classic Negative — lifted shadows, green-cyan mids, trendy stills."""
    return _film_emulation_grade(
        img_rgb,
        filmic=0.55,
        black_lift=0.055,
        contrast=1.08,
        saturation=0.92,
        shadow_tint=(0.03, 0.04, 0.0),
        highlight_tint=(0.02, 0.05, 0.04),
        bloom=0.05,
        grain=0.30,
        vignette=0.13,
        seed=65,
    )


def apply_film_cinestill_50d(img_rgb: np.ndarray) -> np.ndarray:
    """CineStill 50D — daylight cinema, neutral-warm, light halation."""
    return _film_emulation_grade(
        img_rgb,
        filmic=0.44,
        contrast=1.08,
        saturation=0.96,
        shadow_tint=(0.03, 0.02, 0.0),
        highlight_tint=(0.06, 0.04, 0.01),
        bloom=0.12,
        halation=0.06,
        clarity=0.10,
        grain=0.24,
        vignette=0.12,
        seed=66,
    )


def apply_film_hp5_bw(img_rgb: np.ndarray) -> np.ndarray:
    """Ilford HP5 Plus — classic B&W, moderate contrast, visible grain."""
    return _film_emulation_grade(
        img_rgb,
        filmic=0.50,
        contrast=1.12,
        saturation=0.0,
        clarity=0.10,
        grain=0.38,
        vignette=0.18,
        to_bw=True,
        seed=67,
    )


def apply_film_tri_x_bw(img_rgb: np.ndarray) -> np.ndarray:
    """Kodak Tri-X 400 — punchy B&W, deep blacks, gritty grain."""
    return _film_emulation_grade(
        img_rgb,
        filmic=0.40,
        black_lift=0.02,
        contrast=1.22,
        saturation=0.0,
        clarity=0.14,
        grain=0.42,
        vignette=0.20,
        to_bw=True,
        seed=68,
    )


def apply_film_velvia_50(img_rgb: np.ndarray) -> np.ndarray:
    """Fuji Velvia 50 — ultra-saturated landscape slide (greens/blues pop)."""
    return _film_emulation_grade(
        img_rgb,
        filmic=0.40,
        contrast=1.16,
        saturation=1.24,
        shadow_tint=(0.04, 0.02, -0.03),
        highlight_tint=(0.02, 0.06, 0.05),
        bloom=0.06,
        clarity=0.14,
        grain=0.20,
        vignette=0.12,
        seed=69,
    )


def apply_film_superia_400(img_rgb: np.ndarray) -> np.ndarray:
    """Fuji Superia 400 — punchy consumer color, warm shadows, vivid mids."""
    return _film_emulation_grade(
        img_rgb,
        filmic=0.44,
        contrast=1.12,
        saturation=1.18,
        shadow_tint=(-0.02, 0.03, 0.07),
        highlight_tint=(0.07, 0.05, 0.0),
        bloom=0.08,
        clarity=0.11,
        grain=0.26,
        vignette=0.14,
        seed=70,
    )


def apply_film_kodachrome_64(img_rgb: np.ndarray) -> np.ndarray:
    """Kodachrome 64 — rich reds/yellows, deep blues, classic vivid slide."""
    return _film_emulation_grade(
        img_rgb,
        filmic=0.42,
        contrast=1.14,
        saturation=1.20,
        shadow_tint=(0.05, 0.0, -0.04),
        highlight_tint=(0.10, 0.05, -0.02),
        bloom=0.07,
        clarity=0.12,
        grain=0.22,
        vignette=0.11,
        seed=71,
    )


def apply_film_lomo_xpro(img_rgb: np.ndarray) -> np.ndarray:
    """Lomo / cross-process — shifted hues, high saturation, strong vignette."""
    return _film_emulation_grade(
        img_rgb,
        filmic=0.46,
        black_lift=0.035,
        contrast=1.15,
        saturation=1.26,
        shadow_tint=(0.02, 0.06, 0.05),
        highlight_tint=(0.08, -0.02, 0.07),
        bloom=0.05,
        clarity=0.10,
        grain=0.28,
        vignette=0.22,
        seed=72,
    )


def apply_film_ultra_vivid(img_rgb: np.ndarray) -> np.ndarray:
    """Hyper-vivid grade for stage/neon — max clean saturation, low grain."""
    return _film_emulation_grade(
        img_rgb,
        filmic=0.38,
        contrast=1.10,
        saturation=1.30,
        shadow_tint=(0.03, 0.02, 0.05),
        highlight_tint=(0.06, 0.04, 0.02),
        bloom=0.10,
        clarity=0.16,
        grain=0.18,
        vignette=0.08,
        seed=73,
    )


def apply_film_agfa_vista_200(img_rgb: np.ndarray) -> np.ndarray:
    """Agfa Vista 200 — warm European snapshot, rich reds and greens."""
    return _film_emulation_grade(
        img_rgb,
        filmic=0.43,
        contrast=1.13,
        saturation=1.22,
        shadow_tint=(-0.01, 0.04, 0.06),
        highlight_tint=(0.09, 0.04, 0.01),
        bloom=0.09,
        clarity=0.11,
        grain=0.24,
        vignette=0.13,
        seed=74,
    )


def apply_film_astia_100f(img_rgb: np.ndarray) -> np.ndarray:
    """Fuji Astia 100F — vivid slide with softer skin tones, clean blues."""
    return _film_emulation_grade(
        img_rgb,
        filmic=0.41,
        contrast=1.11,
        saturation=1.20,
        shadow_tint=(0.04, 0.03, -0.02),
        highlight_tint=(0.04, 0.05, 0.06),
        bloom=0.08,
        clarity=0.13,
        grain=0.20,
        vignette=0.10,
        seed=75,
    )


def apply_film_polaroid_vivid(img_rgb: np.ndarray) -> np.ndarray:
    """Polaroid-inspired — lifted blacks, candy mids, punchy saturation."""
    return _film_emulation_grade(
        img_rgb,
        filmic=0.50,
        black_lift=0.06,
        contrast=1.08,
        saturation=1.24,
        shadow_tint=(0.03, 0.05, 0.06),
        highlight_tint=(0.08, 0.06, 0.03),
        bloom=0.11,
        clarity=0.08,
        grain=0.30,
        vignette=0.18,
        seed=76,
    )


def apply_film_neon_pop(img_rgb: np.ndarray) -> np.ndarray:
    """Concert neon pop — magenta/cyan push, very high chroma."""
    return _film_emulation_grade(
        img_rgb,
        filmic=0.36,
        contrast=1.12,
        saturation=1.34,
        shadow_tint=(0.06, 0.02, 0.08),
        highlight_tint=(0.05, 0.06, 0.10),
        bloom=0.12,
        halation=0.04,
        clarity=0.14,
        grain=0.16,
        vignette=0.10,
        seed=77,
    )


def apply_film_teal_magenta(img_rgb: np.ndarray) -> np.ndarray:
    """Teal & magenta split — social/cinema vivid (shadows teal, lights warm)."""
    return _film_emulation_grade(
        img_rgb,
        filmic=0.42,
        contrast=1.14,
        saturation=1.28,
        shadow_tint=(0.05, 0.03, 0.02),
        highlight_tint=(0.08, 0.02, 0.07),
        bloom=0.09,
        clarity=0.12,
        grain=0.20,
        vignette=0.14,
        seed=78,
    )


def apply_film_sunset_chrome(img_rgb: np.ndarray) -> np.ndarray:
    """Sunset chrome — golden hour, orange/magenta richness."""
    return _film_emulation_grade(
        img_rgb,
        filmic=0.44,
        contrast=1.12,
        saturation=1.26,
        shadow_tint=(0.02, 0.02, 0.08),
        highlight_tint=(0.12, 0.06, 0.02),
        bloom=0.14,
        clarity=0.10,
        grain=0.22,
        vignette=0.12,
        seed=79,
    )


def apply_film_holga_vivid(img_rgb: np.ndarray) -> np.ndarray:
    """Holga-style — heavy vignette, oversaturated plastic-lens color."""
    return _film_emulation_grade(
        img_rgb,
        filmic=0.48,
        black_lift=0.04,
        contrast=1.10,
        saturation=1.30,
        shadow_tint=(0.04, 0.05, 0.03),
        highlight_tint=(0.07, 0.05, 0.04),
        bloom=0.06,
        clarity=0.06,
        grain=0.32,
        vignette=0.28,
        seed=80,
    )


def apply_film_provia_100f(img_rgb: np.ndarray) -> np.ndarray:
    """Fuji Provia 100F — neutral-vivid slide, clean blues and natural saturation."""
    return _film_emulation_grade(
        img_rgb,
        filmic=0.39,
        contrast=1.13,
        saturation=1.21,
        shadow_tint=(0.04, 0.02, -0.01),
        highlight_tint=(0.03, 0.05, 0.07),
        bloom=0.07,
        clarity=0.15,
        grain=0.18,
        vignette=0.09,
        seed=81,
    )


def apply_film_dutch_golden(img_rgb: np.ndarray) -> np.ndarray:
    """Dutch golden hour — amber highlights, teal shadows, painterly richness."""
    return _film_emulation_grade(
        img_rgb,
        filmic=0.45,
        contrast=1.11,
        saturation=1.23,
        shadow_tint=(0.05, 0.04, 0.0),
        highlight_tint=(0.11, 0.07, 0.02),
        bloom=0.13,
        clarity=0.09,
        grain=0.22,
        vignette=0.11,
        seed=82,
    )


def apply_film_aquamarine_pop(img_rgb: np.ndarray) -> np.ndarray:
    """Aquamarine pop — cool cyan-greens, bright coastal vividness."""
    return _film_emulation_grade(
        img_rgb,
        filmic=0.40,
        contrast=1.12,
        saturation=1.27,
        shadow_tint=(0.05, 0.05, 0.02),
        highlight_tint=(0.02, 0.08, 0.10),
        bloom=0.08,
        clarity=0.13,
        grain=0.19,
        vignette=0.10,
        seed=83,
    )


def apply_film_rose_gold(img_rgb: np.ndarray) -> np.ndarray:
    """Rose gold — pink-gold highlights, lush skin-friendly chroma."""
    return _film_emulation_grade(
        img_rgb,
        filmic=0.43,
        contrast=1.09,
        saturation=1.22,
        shadow_tint=(0.03, 0.02, 0.05),
        highlight_tint=(0.10, 0.05, 0.06),
        bloom=0.12,
        clarity=0.10,
        grain=0.21,
        vignette=0.12,
        seed=84,
    )


def apply_film_expired_slide(img_rgb: np.ndarray) -> np.ndarray:
    """Expired slide — faded lift + surprisingly strong color cast (magenta-green)."""
    return _film_emulation_grade(
        img_rgb,
        filmic=0.52,
        black_lift=0.05,
        contrast=1.06,
        saturation=1.18,
        shadow_tint=(0.04, 0.05, 0.04),
        highlight_tint=(0.06, 0.03, 0.08),
        bloom=0.10,
        clarity=0.07,
        grain=0.34,
        vignette=0.16,
        seed=85,
    )


def apply_film_candy_chrome(img_rgb: np.ndarray) -> np.ndarray:
    """Candy chrome — maximum playful saturation, slight halation, livehouse candy look."""
    return _film_emulation_grade(
        img_rgb,
        filmic=0.35,
        contrast=1.11,
        saturation=1.36,
        shadow_tint=(0.05, 0.03, 0.09),
        highlight_tint=(0.09, 0.05, 0.08),
        bloom=0.14,
        halation=0.05,
        clarity=0.12,
        grain=0.17,
        vignette=0.09,
        seed=86,
    )


def apply_film_neon_tokyo(img_rgb: np.ndarray) -> np.ndarray:
    """Neon Tokyo — blue-magenta street signage, deep shadows (Lab: Film · Neon Tokyo)."""
    return _film_emulation_grade(
        img_rgb,
        filmic=0.37,
        black_lift=0.02,
        contrast=1.15,
        saturation=1.32,
        shadow_tint=(0.07, 0.02, 0.10),
        highlight_tint=(0.04, 0.05, 0.14),
        bloom=0.16,
        halation=0.07,
        clarity=0.13,
        grain=0.20,
        vignette=0.14,
        seed=87,
    )


def apply_film_neon_cyan(img_rgb: np.ndarray) -> np.ndarray:
    """Electric cyan neon — cool blue-green glow, high clarity."""
    return _film_emulation_grade(
        img_rgb,
        filmic=0.36,
        contrast=1.14,
        saturation=1.30,
        shadow_tint=(0.06, 0.04, 0.02),
        highlight_tint=(0.0, 0.10, 0.14),
        bloom=0.14,
        halation=0.05,
        clarity=0.15,
        grain=0.18,
        vignette=0.11,
        seed=90,
    )


def apply_film_neon_magenta(img_rgb: np.ndarray) -> np.ndarray:
    """Hot magenta neon — pink-purple lights, concert poster vibe."""
    return _film_emulation_grade(
        img_rgb,
        filmic=0.38,
        contrast=1.13,
        saturation=1.33,
        shadow_tint=(0.08, 0.02, 0.09),
        highlight_tint=(0.10, 0.03, 0.11),
        bloom=0.15,
        halation=0.06,
        clarity=0.12,
        grain=0.19,
        vignette=0.12,
        seed=91,
    )


def apply_film_neon_club(img_rgb: np.ndarray) -> np.ndarray:
    """Club neon — purple + green lasers, crushed blacks, livehouse floor."""
    return _film_emulation_grade(
        img_rgb,
        filmic=0.35,
        black_lift=0.015,
        contrast=1.17,
        saturation=1.34,
        shadow_tint=(0.06, 0.03, 0.11),
        highlight_tint=(0.03, 0.12, 0.06),
        bloom=0.13,
        halation=0.05,
        clarity=0.14,
        grain=0.22,
        vignette=0.16,
        seed=92,
    )


def apply_film_neon_signage(img_rgb: np.ndarray) -> np.ndarray:
    """Neon signage — warm red signs + cool blue fill, street night mix."""
    return _film_emulation_grade(
        img_rgb,
        filmic=0.39,
        contrast=1.12,
        saturation=1.28,
        shadow_tint=(0.04, 0.02, 0.08),
        highlight_tint=(0.12, 0.04, 0.06),
        bloom=0.17,
        halation=0.08,
        clarity=0.11,
        grain=0.21,
        vignette=0.13,
        seed=93,
    )


def apply_film_neon_haze(img_rgb: np.ndarray) -> np.ndarray:
    """Neon haze — diffused glow, soft halation, dreamy night air."""
    return _film_emulation_grade(
        img_rgb,
        filmic=0.40,
        black_lift=0.03,
        contrast=1.08,
        saturation=1.26,
        shadow_tint=(0.05, 0.03, 0.09),
        highlight_tint=(0.06, 0.06, 0.12),
        bloom=0.20,
        halation=0.10,
        clarity=0.08,
        grain=0.23,
        vignette=0.15,
        seed=94,
    )


def apply_film_mexico_sun(img_rgb: np.ndarray) -> np.ndarray:
    """Mexico sun — hot golden hour, teal-lean shadows, sun-baked saturated color (old cinema)."""
    return _film_emulation_grade(
        img_rgb,
        filmic=0.46,
        black_lift=0.04,
        contrast=1.10,
        saturation=1.20,
        shadow_tint=(0.04, 0.05, 0.03),
        highlight_tint=(0.14, 0.08, 0.02),
        bloom=0.16,
        halation=0.06,
        clarity=0.08,
        grain=0.30,
        vignette=0.17,
        seed=95,
    )


def apply_film_spain_passion(img_rgb: np.ndarray) -> np.ndarray:
    """Spain passion — deep reds, gold highlights, bold contrast, flamenco-era heat."""
    return _film_emulation_grade(
        img_rgb,
        filmic=0.43,
        black_lift=0.03,
        contrast=1.16,
        saturation=1.22,
        shadow_tint=(0.09, 0.01, 0.05),
        highlight_tint=(0.13, 0.07, 0.01),
        bloom=0.12,
        halation=0.07,
        clarity=0.10,
        grain=0.28,
        vignette=0.16,
        seed=96,
    )


def apply_film_latin_cinema(img_rgb: np.ndarray) -> np.ndarray:
    """Latin cinema — faded 35mm base, rich warm-cool split, dusty heat haze (old movie reel)."""
    return _film_emulation_grade(
        img_rgb,
        filmic=0.50,
        black_lift=0.05,
        contrast=1.12,
        saturation=1.16,
        shadow_tint=(0.05, 0.04, 0.06),
        highlight_tint=(0.11, 0.06, 0.03),
        bloom=0.14,
        halation=0.08,
        clarity=0.06,
        grain=0.34,
        vignette=0.19,
        seed=97,
    )


def apply_film_wong_kar_wai(img_rgb: np.ndarray) -> np.ndarray:
    """Wong Kar-wai — humid HK nights: teal-green shadows, amber halation, dreamy contrast (王家卫)."""
    base = _film_emulation_grade(
        img_rgb,
        filmic=0.43,
        black_lift=0.038,
        contrast=1.16,
        saturation=1.06,
        shadow_tint=(0.01, 0.09, 0.07),
        highlight_tint=(0.15, 0.12, 0.02),
        bloom=0.22,
        halation=0.13,
        clarity=0.03,
        grain=0.38,
        vignette=0.24,
        seed=98,
    )
    img = base.astype(np.float32) / 255.0
    lum = _lh_luminance(img)
    mids = np.clip(1.0 - np.abs(lum - 0.42) * 2.1, 0, 1)
    mids = cv2.GaussianBlur(mids, (0, 0), 5)
    img[:, :, 1] += 0.045 * mids
    img[:, :, 2] += 0.028 * mids
    img[:, :, 0] -= 0.022 * mids
    warm = np.clip((lum - 0.55) / 0.45, 0, 1)
    warm = cv2.GaussianBlur(warm, (0, 0), 8)
    img[:, :, 0] += 0.04 * warm
    img[:, :, 1] += 0.028 * warm
    gray = img.mean(axis=2, keepdims=True)
    img = gray + (img - gray) * 0.96
    return np.clip(img * 255.0, 0, 255).astype(np.uint8)


def apply_film_retro_literary_portrait(img_rgb: np.ndarray) -> np.ndarray:
    """Retro literary portrait — matte fade, creamy skin, soft rose warmth (复古文艺人像)."""
    base = _film_emulation_grade(
        img_rgb,
        filmic=0.54,
        black_lift=0.072,
        contrast=0.96,
        saturation=0.86,
        shadow_tint=(0.05, 0.04, 0.05),
        highlight_tint=(0.07, 0.055, 0.045),
        bloom=0.09,
        halation=0.045,
        clarity=0.02,
        grain=0.26,
        vignette=0.14,
        seed=99,
    )
    img = base.astype(np.float32) / 255.0
    lum = _lh_luminance(img)
    skin = np.clip(1.0 - np.abs(lum - 0.52) * 2.4, 0, 1) * np.clip((lum - 0.22) / 0.78, 0, 1)
    skin = cv2.GaussianBlur(skin, (0, 0), 6)
    img[:, :, 0] += 0.022 * skin
    img[:, :, 1] += 0.014 * skin
    img[:, :, 2] += 0.010 * skin
    fade = np.linspace(0.92, 1.0, img.shape[0], dtype=np.float32)[:, None, None]
    img = np.clip(img * fade + (1.0 - fade) * 0.04, 0, 1)
    return np.clip(img * 255.0, 0, 255).astype(np.uint8)


def apply_black_mist_film(img_rgb: np.ndarray) -> np.ndarray:
    """Strong black diffusion + print-film look: wide highlight bloom, halation, density, grain.

    Lab / API variant id: ``film_black_mist``.
    """
    img = img_rgb.astype(np.float32) / 255.0
    h, w = img.shape[:2]
    eps = 1e-6

    # 胶片基底（肩更实、趾略抬）
    img = img / (img + 0.42)
    img = np.clip(img * 0.962 + 0.036, 0, 1)

    lum = 0.2126 * img[:, :, 0] + 0.7152 * img[:, :, 1] + 0.0722 * img[:, :, 2]

    # 轻分裂互补：阴影略青、高光略暖（印放胶片感）
    shadows = np.clip((0.52 - lum) / 0.52, 0, 1)
    highlights = np.clip((lum - 0.50) / 0.50, 0, 1)
    shadows = cv2.GaussianBlur(shadows, (0, 0), 4)
    highlights = cv2.GaussianBlur(highlights, (0, 0), 6)
    img[:, :, 2] += 0.048 * shadows
    img[:, :, 1] += 0.014 * shadows
    img[:, :, 0] -= 0.018 * shadows
    img[:, :, 0] += 0.032 * highlights
    img[:, :, 1] += 0.016 * highlights
    img[:, :, 2] -= 0.020 * highlights
    img = np.clip(img, 0, 1)

    lum = 0.2126 * img[:, :, 0] + 0.7152 * img[:, :, 1] + 0.0722 * img[:, :, 2]

    # 黑柔：更宽的亮权 + 四级模糊（雾更厚）
    bright = np.clip((lum - 0.26) / 0.74, 0, 1)
    bright = np.power(np.maximum(bright, 0.0), 0.78)
    bright = cv2.GaussianBlur(bright, (0, 0), 6)
    layer = img * bright[:, :, None]
    m1 = cv2.GaussianBlur(layer, (0, 0), 14)
    m2 = cv2.GaussianBlur(layer, (0, 0), 32)
    m3 = cv2.GaussianBlur(layer, (0, 0), 52)
    m4 = cv2.GaussianBlur(layer, (0, 0), 78)
    mist = m1 * 0.32 + m2 * 0.28 + m3 * 0.26 + m4 * 0.14
    warm = np.stack([mist[:, :, 0], mist[:, :, 1] * 0.90, mist[:, :, 2] * 0.76], axis=2)
    img = img + warm * 0.72
    img = np.clip(img, 0, 1)

    lum2 = 0.2126 * img[:, :, 0] + 0.7152 * img[:, :, 1] + 0.0722 * img[:, :, 2]

    # 中性「空气」高光层（再糊一层发白柔光）
    airy = np.clip((lum2 - 0.36) / 0.64, 0, 1)
    airy = np.power(airy, 0.85)
    airy = cv2.GaussianBlur(airy, (0, 0), 22)
    img = img + airy[:, :, None] * np.array([0.085, 0.078, 0.072], dtype=np.float32)

    img = np.clip(img, 0, 1)
    lum2b = 0.2126 * img[:, :, 0] + 0.7152 * img[:, :, 1] + 0.0722 * img[:, :, 2]

    # 亮部强柔化局部对比（黑柔主体）
    blur_s = cv2.GaussianBlur(img, (0, 0), 3.2)
    hl_w = np.clip((lum2b - 0.42) / 0.58, 0, 1) ** 0.95
    hl_w = cv2.GaussianBlur(hl_w, (0, 0), 8)
    img = img * (1.0 - 0.22 * hl_w[:, :, None]) + blur_s * (0.22 * hl_w[:, :, None])
    img = np.clip(img, 0, 1)

    lum3 = 0.2126 * img[:, :, 0] + 0.7152 * img[:, :, 1] + 0.0722 * img[:, :, 2]

    # halation 加强
    gx = cv2.Sobel(lum3, cv2.CV_32F, 1, 0)
    gy = cv2.Sobel(lum3, cv2.CV_32F, 0, 1)
    edge = np.sqrt(gx * gx + gy * gy)
    edge = edge / (np.max(edge) + eps)
    spec = np.clip((lum3 - 0.46) / 0.54, 0, 1)
    spec = cv2.GaussianBlur(spec, (0, 0), 6)
    hal = cv2.GaussianBlur(edge * spec, (0, 0), 18)
    img[:, :, 0] += hal * 0.092
    img[:, :, 1] += hal * 0.030

    img = np.clip(img, 0, 1)

    # 印放密度：去饱和更明显
    gray = img.mean(axis=2, keepdims=True)
    img = gray + (img - gray) * 0.84
    img = np.clip(img, 0, 1)

    lum4 = 0.2126 * img[:, :, 0] + 0.7152 * img[:, :, 1] + 0.0722 * img[:, :, 2]

    # 中调微结构（胶片「咬」一点，避免只剩糊）
    blur_m = cv2.GaussianBlur(img, (0, 0), 2.4)
    mid_w = np.clip(1.0 - np.abs(lum4 - 0.48) * 2.35, 0, 1)
    img = img + (img - blur_m) * (0.07 * mid_w[:, :, None])

    img = np.clip(img, 0, 1)
    lum5 = 0.2126 * img[:, :, 0] + 0.7152 * img[:, :, 1] + 0.0722 * img[:, :, 2]

    # 颗粒：细 + 粗 + 色噪
    grain_l = _render_rng(DEFAULT_RENDER_SEED, 56).normal(0, 0.016, (h, w, 1)).astype(np.float32)
    grain_coarse = cv2.GaussianBlur(
        _render_rng(DEFAULT_RENDER_SEED, 57).normal(0, 1.0, (h, w, 1)).astype(np.float32),
        (0, 0),
        1.8,
    )
    if grain_coarse.ndim == 2:
        grain_coarse = grain_coarse[:, :, np.newaxis]
    grain_c = _render_rng(DEFAULT_RENDER_SEED, 58).normal(0, 0.0075, (h, w, 3)).astype(np.float32)
    grain = grain_l * 0.72 + grain_coarse * 0.032 + grain_c * 0.88
    grain = cv2.GaussianBlur(grain, (0, 0), 0.58)
    gmask = np.clip(1.0 - np.abs(lum5 - 0.5) * 1.75, 0, 1)
    gmask = np.clip(gmask + np.clip(1.0 - lum5, 0, 1) * 0.38, 0, 1)
    img += grain * gmask[:, :, None] * 1.38

    y, x = np.indices((h, w), dtype=np.float32)
    cx, cy = w / 2, h / 2
    dist = np.sqrt((x - cx) ** 2 + (y - cy) ** 2)
    dist /= dist.max() + eps
    img *= (1.0 - np.power(dist, 1.38) * 0.20)[:, :, None]

    img = np.clip(img, 0, 1)
    return np.ascontiguousarray((img * 255).astype(np.uint8))


def apply_cinestill_800t(img_rgb: np.ndarray) -> np.ndarray:
    img = img_rgb.astype(np.float32) / 255.0
    h, w = img.shape[:2]
    eps = 1e-6

    # ---------------------------
    # 1. Filmic Tone（核心：压缩高光）
    # ---------------------------
    img = img / (img + 0.42)
    img = img * 0.95 + 0.05

    # ---------------------------
    # 2. Luminance
    # ---------------------------
    lum = 0.2126*img[:,:,0] + 0.7152*img[:,:,1] + 0.0722*img[:,:,2]

    shadows = np.clip((0.55 - lum)/0.55, 0, 1)
    highlights = np.clip((lum - 0.6)/0.4, 0, 1)

    # ---------------------------
    # 3. 色彩模型（Cinestill核心）
    # ---------------------------
    # 阴影：蓝青（冷）
    img[:,:,2] += 0.18 * shadows
    img[:,:,1] += 0.06 * shadows
    img[:,:,0] -= 0.05 * shadows

    # 高光：橙红（暖）
    img[:,:,0] += 0.22 * highlights
    img[:,:,1] += 0.08 * highlights
    img[:,:,2] -= 0.06 * highlights

    # ---------------------------
    # 4. 对比（增加张力）
    # ---------------------------
    img = np.clip((img - 0.5) * 1.3 + 0.5, 0, 1)

    # ---------------------------
    # 5. 高光掩膜（统一光系统）
    # ---------------------------
    bright_mask = np.clip((lum - 0.65)/0.35, 0, 1)
    bright_mask = cv2.GaussianBlur(bright_mask, (0,0), 5)

    # ---------------------------
    # 6. Bloom（光炸开）
    # ---------------------------
    bloom = cv2.GaussianBlur(bright_mask, (0,0), 14)
    img += bloom[:,:,None] * 0.55

    # ---------------------------
    # 7. Halation（真实红晕）
    # ---------------------------
    gx = cv2.Sobel(lum, cv2.CV_32F, 1, 0)
    gy = cv2.Sobel(lum, cv2.CV_32F, 0, 1)
    edge = np.sqrt(gx*gx + gy*gy)
    edge = edge / (np.max(edge) + eps)

    halation_mask = edge * bright_mask
    halation_mask = cv2.GaussianBlur(halation_mask, (0,0), 6)

    halation = cv2.GaussianBlur(halation_mask, (0,0), 20)

    # 👉 红晕（核心）
    img[:,:,0] += halation * 0.32
    img[:,:,1] += halation * 0.06

    # ---------------------------
    # 8. Clarity（防止糊）
    # ---------------------------
    blur_small = cv2.GaussianBlur(img, (0,0), 2)
    img = img + (img - blur_small) * 0.22

    # ---------------------------
    # 9. 主体提亮（轻微）
    # ---------------------------
    y, x = np.indices((h, w))
    cx, cy = w/2, h/2
    dist = np.sqrt((x-cx)**2 + (y-cy)**2)
    dist /= dist.max()

    center_weight = 1 - dist**1.6

    subject_mask = (0.6*lum + 0.4*center_weight)
    subject_mask = cv2.GaussianBlur(subject_mask, (0,0), 15)

    img += subject_mask[:,:,None] * 0.12

    # ---------------------------
    # 10. 胶片颗粒（重点优化）
    # ---------------------------
    # 亮度颗粒（主导）
    grain_luma = _render_rng(DEFAULT_RENDER_SEED, 59).normal(0, 0.028, (h,w,1))

    # 彩色颗粒（弱）
    grain_color = _render_rng(DEFAULT_RENDER_SEED, 60).normal(0, 0.008, (h,w,3))

    grain = grain_luma + grain_color

    # 👉 颗粒块状结构
    grain = cv2.GaussianBlur(grain, (0,0), 1.1)

    # 👉 分布（中灰最多 + 暗部次之）
    grain_mid = np.clip(1 - np.abs(lum - 0.5)*2, 0,1)
    grain_shadow = np.clip(1 - lum, 0,1) * 0.4

    grain_mask = np.clip(grain_mid + grain_shadow, 0,1)

    img += grain * grain_mask[:,:,None]

    # 👉 颗粒对比增强
    img = img + (img - cv2.GaussianBlur(img, (0,0), 1.0)) * 0.12

    # ---------------------------
    # 11. 暗角（情绪）
    # ---------------------------
    vignette = 1 - dist**1.3 * 0.45
    img *= vignette[:,:,None]

    # ---------------------------
    # Final
    # ---------------------------
    img = np.clip(img, 0, 1)
    return (img * 255).astype(np.uint8)


def apply_livehouse_documentary_humanistic(img_rgb: np.ndarray) -> np.ndarray:
    """
    强人文纪实向：可见的粗颗粒胶片、自然光比与暗部，
    通过软肤色保护保留血色与写实肤质，削弱舞台糖水滤镜。
    """
    img = img_rgb.astype(np.float32) / 255.0
    h, w = img.shape[:2]
    eps = 1e-6

    # 1. 温和片比曲线：保留现场层次，略像冲扫片基
    img = img / (img + 0.52)
    img = np.clip(img * 0.92 + 0.04, 0, 1)

    lum = 0.2126 * img[:, :, 0] + 0.7152 * img[:, :, 1] + 0.0722 * img[:, :, 2]

    shadows = np.clip((0.48 - lum) / 0.48, 0, 1)
    highlights = np.clip((lum - 0.62) / 0.38, 0, 1)

    # 2. 极轻冷暖：阴影微青灰、高光微奶黄，不做重对撞
    img[:, :, 2] += 0.055 * shadows
    img[:, :, 1] += 0.018 * shadows
    img[:, :, 0] -= 0.028 * shadows

    img[:, :, 0] += 0.045 * highlights
    img[:, :, 1] += 0.022 * highlights
    img[:, :, 2] -= 0.025 * highlights

    img = np.clip(img, 0, 1)
    lum = 0.2126 * img[:, :, 0] + 0.7152 * img[:, :, 1] + 0.0722 * img[:, :, 2]

    # 3a. 肤色似然（软权重）：偏暖、R 占优且落在常见曝光的肉色区间，不做硬抠图
    r = img[:, :, 0]
    g = img[:, :, 1]
    b = img[:, :, 2]
    warm_rg = np.clip((r - g) * 4.2 + 0.04, 0, 1)
    warm_rb = np.clip((r - b) * 3.4, 0, 1)
    warm_gb = np.clip((g - b) * 2.6 + 0.06, 0, 1)
    skin_like = warm_rg * warm_rb * np.clip(warm_gb, 0, 1)
    mid_exp = np.clip((lum - 0.14) / 0.66, 0, 1) * np.clip((0.92 - lum) / 0.46, 0, 1)
    skin_like *= mid_exp
    skin_like = np.clip(skin_like, 0, 1).astype(np.float32)
    kblur = max(3, min(h, w) // 260)
    if kblur % 2 == 0:
        kblur += 1
    skin_like = cv2.GaussianBlur(skin_like, (kblur, kblur), 0)
    skin_like = np.clip(skin_like, 0, 1)

    # 3b. 全局略降饱和，肤色区明显少降（避免灰脸）
    gray = img.mean(axis=2, keepdims=True)
    sat_keep = 0.73 + 0.26 * skin_like
    img = gray + (img - gray) * sat_keep[:, :, np.newaxis]
    img = np.clip(img, 0, 1)

    # 3c. 写实血色：仅在肤色权重高处轻微偏暖、略压冷灰
    img[:, :, 0] += 0.022 * skin_like
    img[:, :, 1] += 0.008 * skin_like
    img[:, :, 2] -= 0.018 * skin_like
    img = np.clip(img, 0, 1)

    # 3d. 阴影里的肤色：抵消 Step2 全局冷阴影带来的灰绿脸，轻微拉回血色
    lum_sf = 0.2126 * img[:, :, 0] + 0.7152 * img[:, :, 1] + 0.0722 * img[:, :, 2]
    in_shadow = np.clip((0.52 - lum_sf) / 0.52, 0, 1)
    heal = in_shadow * skin_like
    img[:, :, 0] += 0.014 * heal
    img[:, :, 1] += 0.005 * heal
    img[:, :, 2] -= 0.016 * heal
    img = np.clip(img, 0, 1)

    # 4. 适中反差（张力来自颗粒与黑白跨度，不靠 HDR）
    img = np.clip((img - 0.5) * 1.24 + 0.5, 0, 1)

    lum = 0.2126 * img[:, :, 0] + 0.7152 * img[:, :, 1] + 0.0722 * img[:, :, 2]

    # 5. 仅极高光一点点晕（克制，仿灯具溢出而非图层 bloom）
    bright_mask = np.clip((lum - 0.82) / 0.18, 0, 1)
    bright_mask = cv2.GaussianBlur(bright_mask, (0, 0), 6)
    img = np.clip(img + bright_mask[:, :, None] * 0.14, 0, 1)

    lum = 0.2126 * img[:, :, 0] + 0.7152 * img[:, :, 1] + 0.0722 * img[:, :, 2]

    # 6. 轻度局部反差：肌理但不镶边
    blur_mid = cv2.GaussianBlur(img, (0, 0), 2.8)
    img = img + (img - blur_mid) * 0.14

    img = np.clip(img, 0, 1)
    lum = 0.2126 * img[:, :, 0] + 0.7152 * img[:, :, 1] + 0.0722 * img[:, :, 2]

    # 7. 粗颗粒：细 + 粗模糊 + 块状粗粒（push ISO / 粗颗粒胶片观感）
    grain_fine = _render_rng(DEFAULT_RENDER_SEED, 61).normal(0, 1, (h, w, 1)).astype(np.float32)
    grain_mid = cv2.GaussianBlur(
        _render_rng(DEFAULT_RENDER_SEED, 62).normal(0, 1, (h, w, 1)).astype(np.float32), (0, 0), 5.0
    )
    if grain_mid.ndim == 2:
        grain_mid = grain_mid[:, :, None]

    sh = max(2, h // 6)
    sw = max(2, w // 6)
    chunk_seed = _render_rng(DEFAULT_RENDER_SEED, 63).normal(0, 1, (sh, sw, 1)).astype(np.float32)
    grain_chunk = cv2.resize(chunk_seed, (w, h), interpolation=cv2.INTER_NEAREST)
    # OpenCV 单通道 resize 常为 (h,w)，需与 (h,w,1) 对齐以便广播
    if grain_chunk.ndim == 2:
        grain_chunk = grain_chunk[:, :, np.newaxis]

    grain_mix = 0.28 * grain_fine + 0.42 * grain_mid + 0.30 * grain_chunk

    sigma = 0.034
    grain_luma = grain_mix * sigma
    grain_color = _render_rng(DEFAULT_RENDER_SEED, 64).normal(0, sigma * 0.45, (h, w, 3)).astype(np.float32)
    # 肤色区彩噪更明显会显“脏”，压低色度颗粒、保留亮度粗粒
    chroma_sup = 1.0 - 0.42 * skin_like[:, :, np.newaxis]
    grain_color *= chroma_sup

    # 整个亮度跨度都有颗粒；阴影与中灰更重（纪实冲洗）
    mid = np.clip(1.0 - np.abs(lum - 0.48) * 1.85, 0, 1)
    shadow_w = np.clip(1.0 - lum, 0, 1)
    grain_mask = np.clip(0.35 + 0.95 * mid + 0.55 * shadow_w, 0, 1)

    img += (grain_luma + grain_color) * grain_mask[:, :, None]

    # 8. 颗粒后再轻微压高光、稳黑白场（更像实物印放）
    img = np.clip(img, 0, 1)
    img = np.clip((img - 0.5) * 1.06 + 0.5, 0, 1)
    toe = 0.028
    img = img * (1.0 - toe) + toe * 0.35

    img = np.clip(img, 0, 1)

    # 9. 很轻暗角：聚拢但不戏剧舞台化
    y, x = np.indices((h, w), dtype=np.float32)
    cx, cy = w / 2, h / 2
    dist = np.sqrt((x - cx) ** 2 + (y - cy) ** 2)
    dist = dist / (np.max(dist) + eps)
    vignette = 1 - np.power(dist, 1.35) * 0.26
    img *= vignette[:, :, None]

    img = np.clip(img, 0, 1)
    return (img * 255).astype(np.uint8)


def apply_livehouse_festival_documentary(img_rgb: np.ndarray) -> np.ndarray:
    """
    音乐节现场纪实向成片：浓重冷暖对撞、舞台体积光、边缘色散与粗颗粒，
    偏高对比与局部反差，弱化“塑料 HDR”，偏粗粝真实。
    """
    img = img_rgb.astype(np.float32) / 255.0
    h, w = img.shape[:2]
    eps = 1e-6

    # 1. Filmic 底座：略压高光、暗部留出细节再后面用对比压回去
    img = img / (img + 0.48)
    img = np.clip(img * 0.94 + 0.06, 0, 1)

    lum = 0.2126 * img[:, :, 0] + 0.7152 * img[:, :, 1] + 0.0722 * img[:, :, 2]

    shadows = np.clip((0.52 - lum) / 0.52, 0, 1)
    highlights = np.clip((lum - 0.58) / 0.42, 0, 1)

    # 2. 冷暖对撞（阴影青蓝 / 高光琥珀），略加重于 Cinestill 以贴现场霓虹
    img[:, :, 2] += 0.20 * shadows
    img[:, :, 1] += 0.07 * shadows
    img[:, :, 0] -= 0.06 * shadows

    img[:, :, 0] += 0.26 * highlights
    img[:, :, 1] += 0.09 * highlights
    img[:, :, 2] -= 0.07 * highlights

    img = np.clip(img, 0, 1)

    # 3. S 曲线 — 张力
    img = np.clip((img - 0.5) * 1.42 + 0.5, 0, 1)

    lum = 0.2126 * img[:, :, 0] + 0.7152 * img[:, :, 1] + 0.0722 * img[:, :, 2]

    # 4. 多级 bloom：点光 + 体积光晕
    bright_mask = np.clip((lum - 0.62) / 0.38, 0, 1)
    bright_mask = cv2.GaussianBlur(bright_mask, (0, 0), 4)
    bm_small = cv2.GaussianBlur(bright_mask, (0, 0), 8)
    bm_large = cv2.GaussianBlur(bright_mask, (0, 0), 22)
    bloom = np.clip(bm_small * 0.55 + bm_large * 0.45, 0, 1)
    img = np.clip(img + bloom[:, :, None] * 0.62, 0, 1)

    # 5. Halation：高反差边上的胶片红溢
    gx = cv2.Sobel(lum, cv2.CV_32F, 1, 0)
    gy = cv2.Sobel(lum, cv2.CV_32F, 0, 1)
    edge = np.sqrt(gx * gx + gy * gy)
    edge = edge / (np.max(edge) + eps)
    halation_mask = edge * bright_mask
    halation_mask = cv2.GaussianBlur(halation_mask, (0, 0), 5)
    halation = cv2.GaussianBlur(halation_mask, (0, 0), 24)
    img[:, :, 0] += halation * 0.38
    img[:, :, 1] += halation * 0.07

    img = np.clip(img, 0, 1)

    # 6. 极轻边缘色散（纪实向，非迷幻 CA）
    y, x = np.indices((h, w), dtype=np.float32)
    cx, cy = w / 2, h / 2
    dx = (x - cx) / max(cx, eps)
    dy = (y - cy) / max(cy, eps)
    dist = np.sqrt(dx * dx + dy * dy)
    dist_norm = dist / (np.max(dist) + eps)
    edge_ramp = np.power(dist_norm, 1.75)
    unit_x = dx / (dist + eps)
    unit_y = dy / (dist + eps)
    img = _stage_dream_flavor(
        img,
        lum,
        x,
        y,
        unit_x,
        unit_y,
        dist_norm,
        edge_ramp,
        ghost_strength=0.0,
        ca_strength=0.35,
        psychedelic_strength=0.0,
        highlight_hint=bright_mask,
        seed=DEFAULT_RENDER_SEED,
        eps=eps,
    )

    img = np.clip(img, 0, 1)

    # 7. 局部反差 — 粗粝肌理与“拍实了”的感觉
    blur_small = cv2.GaussianBlur(img, (0, 0), 2)
    img = img + (img - blur_small) * 0.28
    blur_hi = cv2.GaussianBlur(img, (0, 0), 0.9)
    img = img + (img - blur_hi) * 0.10

    lum = 0.2126 * img[:, :, 0] + 0.7152 * img[:, :, 1] + 0.0722 * img[:, :, 2]

    # 8. 多尺度颗粒：中灰 + 暗部更重
    grain_fine = _render_rng(DEFAULT_RENDER_SEED, 65).normal(0, 1, (h, w, 1)).astype(np.float32)
    grain_coarse = cv2.GaussianBlur(
        _render_rng(DEFAULT_RENDER_SEED, 66).normal(0, 1, (h, w, 1)).astype(np.float32), (0, 0), 2.8
    )
    if grain_coarse.ndim == 2:
        grain_coarse = grain_coarse[:, :, None]
    grain_mix = 0.55 * grain_fine + 0.45 * grain_coarse
    sigma = 0.018
    grain_luma = grain_mix * sigma
    grain_color = _render_rng(DEFAULT_RENDER_SEED, 67).normal(0, sigma * 0.75, (h, w, 3)).astype(np.float32)
    grain_mid = np.clip(1 - np.abs(lum - 0.5) * 2, 0, 1)
    grain_shadow = np.clip(1 - lum, 0, 1) * 0.55
    grain_mask = np.clip(grain_mid * 1.1 + grain_shadow, 0, 1)
    img += (grain_luma + grain_color) * grain_mask[:, :, None]

    # 9. 暗角聚拢视线
    dist = np.sqrt((x - cx) ** 2 + (y - cy) ** 2)
    dist = dist / (np.max(dist) + eps)
    vignette = 1 - np.power(dist, 1.25) * 0.52
    img *= vignette[:, :, None]

    img = np.clip(img, 0, 1)
    return (img * 255).astype(np.uint8)


def apply_ricoh_gr_positive_film(img_rgb: np.ndarray) -> np.ndarray:
    """Ricoh GR «Positive Film»–style grade: punchy contrast, clean blacks, street snap.

    Mimics in-camera Positive Film (not RAW): lifted toe, firm shoulder, slight global
    saturation lift, cool-teal shadows, neutral-warm highlights, fine grain, mild vignette.
    Lab / API variant id: ``film_ricoh_gr``.
    """
    img = img_rgb.astype(np.float32) / 255.0
    h, w = img.shape[:2]
    eps = 1e-6

    # 1. Filmic base + toe lift (GR keeps shadow detail readable)
    img = img / (img + 0.50)
    img = np.clip(img * 0.985 + 0.028, 0, 1)

    lum = 0.2126 * img[:, :, 0] + 0.7152 * img[:, :, 1] + 0.0722 * img[:, :, 2]

    shadows = np.clip((0.54 - lum) / 0.54, 0, 1)
    shadows = cv2.GaussianBlur(shadows, (0, 0), 4)
    mids = np.clip(1.0 - np.abs(lum - 0.46) / 0.38, 0, 1) ** 1.1
    highlights = np.clip((lum - 0.58) / 0.42, 0, 1)
    highlights = cv2.GaussianBlur(highlights, (0, 0), 5)

    # 2. Positive Film color: shadow teal-green, mids saturated, highlights clean
    img[:, :, 2] += 0.038 * shadows
    img[:, :, 1] += 0.012 * shadows
    img[:, :, 0] -= 0.014 * shadows

    img[:, :, 0] += 0.022 * mids
    img[:, :, 1] += 0.028 * mids
    img[:, :, 2] += 0.018 * mids

    img[:, :, 0] += 0.012 * highlights
    img[:, :, 1] += 0.010 * highlights
    img[:, :, 2] -= 0.008 * highlights
    img = np.clip(img, 0, 1)

    # 3. Saturation bump in midtones (Positive Film «鲜活»)
    gray = img.mean(axis=2, keepdims=True)
    sat_w = np.clip(mids + 0.35 * highlights, 0, 1)
    sat_w = cv2.GaussianBlur(sat_w, (0, 0), 3)
    img = gray + (img - gray) * (1.0 + 0.14 * sat_w[:, :, None])
    hl_desat = np.clip((lum - 0.72) / 0.28, 0, 1)
    img = gray + (img - gray) * (1.0 - 0.12 * hl_desat[:, :, None])
    img = np.clip(img, 0, 1)

    lum2 = 0.2126 * img[:, :, 0] + 0.7152 * img[:, :, 1] + 0.0722 * img[:, :, 2]

    # 4. GR snap: local contrast on mids (28mm street clarity)
    blur_m = cv2.GaussianBlur(img, (0, 0), 2.2)
    snap_w = np.clip(1.0 - np.abs(lum2 - 0.48) * 2.2, 0, 1)
    img = img + (img - blur_m) * (0.20 * snap_w[:, :, None])
    img = np.clip(img, 0, 1)

    # 5. Gentle highlight roll-off (avoid digital clip)
    spec = np.clip((lum2 - 0.78) / 0.22, 0, 1)
    spec = cv2.GaussianBlur(spec, (0, 0), 6)
    img = img * (1.0 - 0.08 * spec[:, :, None]) + 0.08 * spec[:, :, None]
    img = np.clip(img, 0, 1)

    lum3 = 0.2126 * img[:, :, 0] + 0.7152 * img[:, :, 1] + 0.0722 * img[:, :, 2]

    # 6. Fine grain (Positive Film has visible but fine structure)
    grain_l = _render_rng(DEFAULT_RENDER_SEED, 68).normal(0, 0.014, (h, w, 1)).astype(np.float32)
    grain_c = _render_rng(DEFAULT_RENDER_SEED, 69).normal(0, 0.006, (h, w, 3)).astype(np.float32)
    grain = grain_l + grain_c
    grain = cv2.GaussianBlur(grain, (0, 0), 0.85)
    gmask = np.clip(1.0 - np.abs(lum3 - 0.5) * 1.85, 0, 1)
    gmask = np.clip(gmask + np.clip(1.0 - lum3, 0, 1) * 0.22, 0, 1)
    img += grain * gmask[:, :, None] * 1.15

    # 7. Mild corner vignette (GR lens character, subtle)
    y, x = np.indices((h, w), dtype=np.float32)
    cx, cy = w / 2, h / 2
    dist = np.sqrt((x - cx) ** 2 + (y - cy) ** 2)
    dist /= dist.max() + eps
    vignette = 1.0 - np.power(dist, 1.35) * 0.16
    img *= vignette[:, :, None]

    # 8. Slight S-curve finish
    img = np.clip(np.power(np.maximum(img, 0.0), 0.98), 0, 1)

    return np.ascontiguousarray((img * 255).astype(np.uint8))
