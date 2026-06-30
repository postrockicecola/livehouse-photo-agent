import cv2
import torch
import numpy as np
from PIL import Image
import rawpy

def luma_advanced_inpainting_workflow(raw_path, out_path):
    print(f"🪄 正在执行【无痕修补 + 大师级影调】全流程: {raw_path}")
    
    # --- Step 1: 获取 16-bit 线性数据 (审美重构口粮) ---
    with rawpy.imread(raw_path) as raw:
        rgb_linear = raw.postprocess(use_camera_wb=True, no_auto_bright=True, user_flip=True, output_bps=16)

    # --- Step 2: 运行审美算子 (先调色，让烟雾质感出来) ---
    data = torch.from_numpy(rgb_linear.astype(np.float32) / 65535.0)
    
    # [此处保持你那 9步大师级修图指南 的核心逻辑不变]
    luma = data.mean(dim=-1, keepdim=True)
    y = torch.sigmoid(12 * (data - 0.4))
    subject_mask = torch.exp(-((luma - 0.45)**2) / 0.08)
    y = y * (1.4 * subject_mask + 0.75 * (1 - subject_mask))
    y[..., 0] *= 0.96; y[..., 2] *= 1.04
    shadow_mask = torch.pow(1.0 - luma, 3)
    y[..., 2] += (shadow_mask * 0.05).squeeze(-1)
    highlight_mask = torch.pow(luma, 2)
    y[..., 0] += (highlight_mask * 0.04).squeeze(-1)
    
    processed_tensor = torch.pow(y.clamp(0, 1), 1.0/1.6) # Gamma 稍微沉稳一点

    # --- Step 3: 【无痕修补】 (Image Inpainting) ---
    # 转换为 OpenCV 格式的 8-bit BGR (修补算子的口粮)
    res_np = (processed_tensor.numpy() * 255.0).clip(0, 255).astype(np.uint8)
    img_bgr = cv2.cvtColor(res_np, cv2.COLOR_RGB2BGR)
    h, w, _ = img_bgr.size
    
    # A. 自动生成遮罩 (Mask)
    # 我们知道观众在底部，且很黑。
    luma_np = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    # 设定位置：底部 20%
    roi_top = int(h * 0.8)
    roi_luma = luma_np[roi_top:, :]
    # 设定亮度：亮度低于 40 的认定为观众黑头
    mask_roi = cv2.threshold(roi_luma, 40, 255, cv2.THRESH_BINARY_INV)[1]
    
    # 构造完整的 Mask [H, W, 1]
    mask = np.zeros((h, w), dtype=np.uint8)
    mask[roi_top:, :] = mask_roi
    
    # 膨胀遮罩，让它多盖住一点边缘，确保无痕
    kernel = np.ones((7, 7), np.uint8)
    mask = cv2.dilate(mask, kernel, iterations=2)
    
    # 
    # 这个 Mask 就像一个精准的“黑色高通滤波”，它把所有的黑头都吸了进去。

    # B. 执行传统修补 (使用 Telea 算法，对光影过渡处理较好)
    print(f"🪄 正在用 Telea 算法填充 {int(mask.sum()/255)} 个黑头像素...")
    inpainted_bgr = cv2.inpaint(img_bgr, mask, 5, cv2.INPAINT_TELEA)

    # --- Step 4: 最终裁切 (Step 9) ---
    inpainted_rgb = cv2.cvtColor(inpainted_bgr, cv2.COLOR_BGR2RGB)
    img_final = Image.fromarray(inpainted_rgb)
    
    # 侧边保持三分法，顶部/底部温和裁切 (因为黑头已经被修补了)
    left = w * 0.15
    top = h * 0.05 # 顶部少切点
    right = w * 0.95
    bottom = h * 0.98 # 底部少切点，保留修补后的烟雾
    
    cropped_final = img_final.crop((left, top, right, bottom))
    
    # --- Step 5: 保存成片 ---
    cropped_final.save(out_path, quality=98)
    print(f"✅ 无痕修补的大师级成片已保存: {out_path}")

if __name__ == "__main__":
    luma_advanced_inpainting_workflow("/app/test.ARW", "/app/final_masterpiece_inpainted.jpg")