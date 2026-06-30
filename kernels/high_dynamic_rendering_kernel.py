import torch
import triton
import triton.language as tl

# 自动调优：针对 A7C2 33MP 高分辨率照片优化
# @triton.autotune(
#   configs=[
#        triton.Config({'BLOCK_SIZE_M': 128, 'BLOCK_SIZE_N': 256}, num_warps=8),
#        triton.Config({'BLOCK_SIZE_M': 256, 'BLOCK_SIZE_N': 128}, num_warps=8),
#        triton.Config({'BLOCK_SIZE_M': 128, 'BLOCK_SIZE_N': 128}, num_warps=4),
#    ],
#    key=['IMAGE_N'],
#)

@triton.jit
def _japanese_fresh_kernel(
    image_ptr, output_ptr,
    IMAGE_M, IMAGE_N,
    EV_OFFSET, TARGET_SAT, CONTRAST, # 刚好 7 个
    BLOCK_SIZE_M: tl.constexpr, 
    BLOCK_SIZE_N: tl.constexpr,
):
    # -----------------------------------------------------------
    # 1. 计算当前 Block 的像素坐标
    pid_m = tl.program_id(0)
    pid_n = tl.program_id(1)
    
    # 生成 Block 内的网格坐标
    rm = pid_m * BLOCK_SIZE_M + tl.arange(0, BLOCK_SIZE_M)
    rn = pid_n * BLOCK_SIZE_N + tl.arange(0, BLOCK_SIZE_N)
    
    # 边界检查掩码
    mask = (rm[:, None] < IMAGE_M) & (rn[None, :] < IMAGE_N)
    
    # 计算 Global Memory 指针偏移 (image 是 H, W, 3)
    base_ptr = image_ptr + (rm[:, None] * IMAGE_N + rn[None, :]) * 3
    
    # -----------------------------------------------------------
    # 2. 批量加载 RGB 数据 (Vectorized Load)
    # Triton 会自动将这些独立的 load 指令合并成更高效的访存模式
    r = tl.load(base_ptr + 0, mask=mask, other=0.0)
    g = tl.load(base_ptr + 1, mask=mask, other=0.0)
    b = tl.load(base_ptr + 2, mask=mask, other=0.0)
    
    # -----------------------------------------------------------
    # 3. 基础曝光调整 (硬核融合：将计算算子下推到加载后立即执行)
    # 提升 EV：RGB * (2 ^ EV_offset)
    r = r * EV_OFFSET
    g = g * EV_OFFSET
    b = b * EV_OFFSET
    
    # -----------------------------------------------------------
    # 4. 计算亮度层 (Base Layer) 用于 HDR 压缩
    # 我们这里使用简单的逐像素 Reinhard 算子来压缩高光，提亮阴影。
    
    # 计算新的亮度
    luma = 0.299 * r + 0.587 * g + 0.114 * b
    
    # Reinhard 动态范围压缩增益
    # Gain = 1 / (1 + luma)
    # 这个增益在高光区小，在阴影区大，实现了自适应压缩。
    gain = 1.0 / (1.0 + luma)
    
    # 应用 HDR 压缩
    new_r = r * gain
    new_g = g * gain
    new_b = b * gain
    
    # -----------------------------------------------------------
    # 5. 再次计算新的亮度 (用于饱和度混合)
    new_luma = 0.299 * new_r + 0.587 * new_g + 0.114 * new_b
    
    # -----------------------------------------------------------
    # 6. 应用日系低饱和度风格 (色彩空间融合)
    # 日系的核心在于色彩淡淡的。
    
    # 色彩混合：Output = Luma + (RGB - Luma) * Saturation
    new_r = new_luma + (new_r - new_luma) * TARGET_SAT
    new_g = new_luma + (new_g - new_luma) * TARGET_SAT
    new_b = new_luma + (new_b - new_luma) * TARGET_SAT
    
    # -----------------------------------------------------------
    # 7. 最终数据清理与 Gamma 校正 (模拟真彩色屏幕显示)
    # 展现你对 14-bit 到 8-bit 显示转换的理解。
    
    # 确保不爆白 (Clamping)
    new_r = tl.clamp(new_r, 0.0, 1.0)
    new_g = tl.clamp(new_g, 0.0, 1.0)
    new_b = tl.clamp(new_b, 0.0, 1.0)
    
    # 应用标准 Gamma 2.2：Image ^ (1/2.2)
    gamma_inv = 1.0 / 2.2
    new_r = tl.exp(gamma_inv * tl.log(new_r + 1e-7))
    new_g = tl.exp(gamma_inv * tl.log(new_g + 1e-7))
    new_b = tl.exp(gamma_inv * tl.log(new_b + 1e-7))
    
    # -----------------------------------------------------------
    # 8. 回写数据 (Vectorized Store)
    out_base_ptr = output_ptr + (rm[:, None] * IMAGE_N + rn[None, :]) * 3
    tl.store(out_base_ptr + 0, new_r, mask=mask)
    tl.store(out_base_ptr + 1, new_g, mask=mask)
    tl.store(out_base_ptr + 2, new_b, mask=mask)

# -----------------------------------------------------------
# Python 包装器 (负责 RAW 预处理和 autotune 启动)
def apply_japanese_fresh_pipeline(image_tensor, ev_offset=1.5, target_sat=0.6, contrast=1.2):
    # 1. RAW 预处理：归一化到 [0.0, 1.0] float32
    # 这一步将 14-bit 数据从 uint16 转为通用 float32 空间
    image_float = image_tensor.to(torch.float32) / 16383.0
    
    H, W, C = image_float.shape
    
    # 2. 准备输出 Tensor
    output = torch.empty_like(image_float)
    
    # 3. 计算 Metaparameters：曝光补偿和饱和度
    # 2^EV_offset 的计算下推到 Python 端完成
    ev_gain = 2.0 ** ev_offset
    
    # 4. 启动 Kernel (让 autotune 处理 BLOCK_SIZE)
    grid = lambda META: (
        triton.cdiv(H, META['BLOCK_SIZE_M']),
        triton.cdiv(W, META['BLOCK_SIZE_N']),
    )
    
    # 手动写死 Block Size，解释器模式下小一点比较稳
    BLOCK_SIZE_M = 32
    BLOCK_SIZE_N = 32

    # --- 关键修复点 ---
    _japanese_fresh_kernel[grid](
        image_float,   # 1. image_ptr
        output,        # 2. output_ptr
        H,             # 3. IMAGE_M
        W,             # 4. IMAGE_N
        ev_offset,     # 5. EV_OFFSET
        target_sat,    # 6. TARGET_SAT
        contrast,      # 7. CONTRAST
        BLOCK_SIZE_M=32, 
        BLOCK_SIZE_N=32
    )
    
    # 5. 后处理：转回 8-bit uint8 用于保存
    return (output * 255.0).to(torch.uint8)