# 路径: /app/agents/raw_processor.py
import rawpy
import numpy as np
import os

class RawProcessor:
    @staticmethod
    def to_npy(arw_path, output_dir="data"):
        """
        核心逻辑：将 Sony ARW 转换为 16-bit 线性数据
        """
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
            
        file_base = os.path.basename(arw_path).split('.')[0]
        npy_path = os.path.join(output_dir, f"{file_base}.npy")

        print(f"📦 [RAW Agent] Extracting linear 16-bit data: {arw_path}")

        try:
            with rawpy.imread(arw_path) as raw:
                # 这里使用最纯净的预处理设置：
                # 1. 禁用 Gamma 矫正 (保持线性)
                # 2. 禁用自动亮度
                # 3. 强制 16-bit 输出
                rgb = raw.postprocess(
                    gamma=(1, 1), 
                    no_auto_bright=True, 
                    output_bps=16, 
                    use_camera_wb=True
                )
                np.save(npy_path, rgb)
                print(f"✅ [RAW Agent] Saved to {npy_path}")
                return npy_path
        except Exception as e:
            print(f"❌ [RAW Agent] Error processing {arw_path}: {e}")
            return None

if __name__ == "__main__":
    # 快速测试代码
    processor = RawProcessor()
    # 这里的路径根据你 data 里的实际 arw 调整
    processor.to_npy("data/black_cat.ARW")