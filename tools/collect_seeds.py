import os
import shutil

# --- 配置区 ---
# 原始图片根目录（用 SEEDS_SOURCE_ROOT 覆盖）
SOURCE_ROOT = os.environ.get("SEEDS_SOURCE_ROOT", "/path/to/Pictures")
# 目标存放目录（用 SEEDS_TARGET_DIR 覆盖）
TARGET_DIR = os.environ.get("SEEDS_TARGET_DIR", os.path.join(SOURCE_ROOT, "Livehouse_Seeds"))

def collect_refined_photos():
    if not os.path.exists(TARGET_DIR):
        os.makedirs(TARGET_DIR)
        print(f"📁 已创建目标文件夹: {TARGET_DIR}")

    count = 0
    # 1. 遍历 Pictures 下的所有日期文件夹
    for date_folder in os.listdir(SOURCE_ROOT):
        date_path = os.path.join(SOURCE_ROOT, date_folder)
        
        # 排除掉不是目录的文件（比如 .DS_Store）
        if not os.path.isdir(date_path):
            continue
            
        # 2. 检查该日期文件夹下是否有 'out' 目录
        out_path = os.path.join(date_path, "out")
        if os.path.exists(out_path) and os.path.isdir(out_path):
            print(f"🔍 正在从 {date_folder}/out 中提取照片...")
            
            # 3. 遍历 out 目录下的所有文件
            for file_name in os.listdir(out_path):
                if file_name.lower().endswith(('.jpg', '.jpeg')):
                    src_file = os.path.join(out_path, file_name)
                    
                    # 4. 为了防止不同日期有同名文件（比如都是 1.jpg）
                    # 我们把日期加到文件名前缀：20260329_1.jpg
                    new_file_name = f"{date_folder}_{file_name}"
                    dest_file = os.path.join(TARGET_DIR, new_file_name)
                    
                    shutil.copy2(src_file, dest_file) # copy2 会保留原始修改时间
                    count += 1

    print("-" * 30)
    print(f"✨ 任务完成！共提取了 {count} 张精选照片。")
    print(f"📍 它们现在都躺在这里: {TARGET_DIR}")

if __name__ == "__main__":
    collect_refined_photos()