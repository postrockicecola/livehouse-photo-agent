import torch
import numpy as np

try:
    import triton
    import triton.language as tl
    HAS_TRITON = True
except ImportError:
    HAS_TRITON = False

# --- Triton GPU Kernel 部分 ---
@triton.jit
def _starflare_gpu_kernel(
    input_ptr, output_ptr,
    H, W, stride_h, stride_w,
    threshold, flare_length, flare_strength,
    BLOCK_SIZE: tl.constexpr,
):
    pid_h, pid_w = tl.program_id(0), tl.program_id(1)
    rm = pid_h * BLOCK_SIZE + tl.arange(0, BLOCK_SIZE)
    rn = pid_w * BLOCK_SIZE + tl.arange(0, BLOCK_SIZE)
    mask = (rm[:, None] < H) & (rn[None, :] < W)
    offs = rm[:, None] * stride_h + rn[None, :] * stride_w

    r = tl.load(input_ptr + offs * 3 + 0, mask=mask, other=0.0)
    g = tl.load(input_ptr + offs * 3 + 1, mask=mask, other=0.0)
    b = tl.load(input_ptr + offs * 3 + 2, mask=mask, other=0.0)
    
    luma = 0.299 * r + 0.587 * g + 0.114 * b
    is_bright = luma > threshold

    # 简单演示：基于掩码的增强，GPU上可扩展为更复杂的循环
    res_r = tl.where(is_bright, r * (1.0 + flare_strength), r)
    res_g = tl.where(is_bright, g * (1.0 + flare_strength), g)
    res_b = tl.where(is_bright, b * (1.0 + flare_strength), b)

    tl.store(output_ptr + offs * 3 + 0, res_r, mask=mask)
    tl.store(output_ptr + offs * 3 + 1, res_g, mask=mask)
    tl.store(output_ptr + offs * 3 + 2, res_b, mask=mask)

# --- 封装类 ---
class TritonStarflare:
    def __init__(self, threshold=0.7, flare_length=30, flare_strength=0.5):
        self.threshold = threshold
        self.flare_length = flare_length
        self.flare_strength = flare_strength

    def _cpu_simulate(self, x):
        """[CPU 专属] 完美的十字星扩散，无 Segfault，速度极快"""
        h, w, c = x.shape
        # 计算亮度并提取高光掩码
        luma = 0.299 * x[..., 0] + 0.587 * x[..., 1] + 0.114 * x[..., 2]
        mask = (luma > self.threshold).float().unsqueeze(-1)
        
        out = x.clone()
        # 核心：对称扩散（上下左右）
        for i in range(1, self.flare_length):
            weight = self.flare_strength * (1.0 - i / self.flare_length) * 0.25
            # Right & Left
            out[:, i:, :] += x[:, :-i, :] * mask[:, :-i, :] * weight
            out[:, :-i, :] += x[:, i:, :] * mask[:, i:, :] * weight
            # Down & Up
            out[i:, :, :] += x[:-i, :, :] * mask[:-i, :, :] * weight
            out[:-i, :, :] += x[i:, :, :] * mask[i:, :, :] * weight
            
        return out.clamp(0, 1.0)

    def __call__(self, x: torch.Tensor):
        x = x.contiguous()
        if not x.is_cuda:
            return self._cpu_simulate(x)
        
        # GPU 路径
        H, W, C = x.shape
        out = torch.empty_like(x)
        BLOCK_SIZE = 16
        grid = (triton.cdiv(H, BLOCK_SIZE), triton.cdiv(W, BLOCK_SIZE))
        _starflare_gpu_kernel[grid](
            x, out, H, W, x.stride(0), x.stride(1),
            self.threshold, self.flare_length, self.flare_strength,
            BLOCK_SIZE=BLOCK_SIZE
        )
        return out