import triton
import triton.language as tl
import torch

@triton.jit
def _retro_film_kernel(
    image_ptr, output_ptr,
    IMAGE_M, IMAGE_N,
    EV_OFFSET, TARGET_SAT, CONTRAST,
    BLOCK_SIZE_M: tl.constexpr, 
    BLOCK_SIZE_N: tl.constexpr,
):
    # 1. 坐标与加载
    row = tl.program_id(0) * BLOCK_SIZE_M + tl.arange(0, BLOCK_SIZE_M)
    col = tl.program_id(1) * BLOCK_SIZE_N + tl.arange(0, BLOCK_SIZE_N)
    mask = (row[:, None] < IMAGE_M) & (col[None, :] < IMAGE_N)
    offset = (row[:, None] * IMAGE_N + col[None, :]) * 3
    
    r = tl.load(image_ptr + offset, mask=mask)
    g = tl.load(image_ptr + offset + 1, mask=mask)
    b = tl.load(image_ptr + offset + 2, mask=mask)

    # 2. 基础曝光补偿
    r = r * EV_OFFSET * 1.1
    g = g * EV_OFFSET * 1.05
    b = b * EV_OFFSET

    # 3. 稳健的混色逻辑 (让橙色更厚重，且不偏紫)
    r_mix = r + g * 0.05
    g_mix = g + r * 0.02
    b_mix = b

    # 4. 手动内联 ACES Tone Mapping (彻底解决 Defined 报错)
    # R 通道
    r = (r_mix * (2.51 * r_mix + 0.02)) / (r_mix * (2.43 * r_mix + 0.50) + 0.10)
    # G 通道
    g = (g_mix * (2.51 * g_mix + 0.02)) / (g_mix * (2.43 * g_mix + 0.50) + 0.10)
    # B 通道
    b = (b_mix * (2.51 * b_mix + 0.02)) / (b_mix * (2.43 * b_mix + 0.50) + 0.10)

    # 5. 暴力饱和度提升 (1.6x 冲击力)
    luma = 0.299 * r + 0.587 * g + 0.114 * b
    sat_scale = TARGET_SAT * 1.6
    r = luma + (r - luma) * sat_scale
    g = luma + (g - luma) * sat_scale
    b = luma + (b - luma) * sat_scale

    # 6. 非线性 Gamma 对比度
    gamma_p = 0.4545 * CONTRAST
    r = tl.exp(gamma_p * tl.log(tl.maximum(1e-6, r)))
    g = tl.exp(gamma_p * tl.log(tl.maximum(1e-6, g)))
    b = tl.exp(gamma_p * tl.log(tl.maximum(1e-6, b)))

    # 7. 亮部保护：确保不会因为对比度拉太高而导致高光死白
    r = tl.minimum(1.0, r)
    g = tl.minimum(1.0, g)
    b = tl.minimum(1.0, b)

    # 8. 限制范围并回写为 uint8
    r_out = tl.maximum(0.0, tl.minimum(255.0, r * 255.0)).to(tl.uint8)
    g_out = tl.maximum(0.0, tl.minimum(255.0, g * 255.0)).to(tl.uint8)
    b_out = tl.maximum(0.0, tl.minimum(255.0, b * 255.0)).to(tl.uint8)

    tl.store(output_ptr + offset, r_out, mask=mask)
    tl.store(output_ptr + offset + 1, g_out, mask=mask)
    tl.store(output_ptr + offset + 2, b_out, mask=mask)

def apply_retro_film_pipeline(image_tensor, ev=1.2, target_sat=1.0, contrast=1.3):
    image_float = image_tensor.to(torch.float32) / 16383.0
    H, W, _ = image_float.shape
    output = torch.empty((H, W, 3), device=image_tensor.device, dtype=torch.uint8)

    grid = lambda meta: (triton.cdiv(H, meta['BLOCK_SIZE_M']), 
                         triton.cdiv(W, meta['BLOCK_SIZE_N']))

    _retro_film_kernel[grid](
        image_float, output, H, W,
        ev, target_sat, contrast,
        BLOCK_SIZE_M=32, BLOCK_SIZE_N=32
    )
    return output