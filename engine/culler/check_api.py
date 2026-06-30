import os
import sys
import shutil
import numpy as np
import grpc
from openai import OpenAI
import json
import re

# 强制将 /code 加入路径，确保能找到 api.gen.python
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))
from api.gen.python import vision_pb2, vision_pb2_grpc

# ================= 配置区 =================
# 1. 通义千问配置 (DashScope) — 密钥从环境变量读取，不要硬编码提交
QWEN_API_KEY = os.environ.get("DASHSCOPE_API_KEY", "")
QWEN_BASE_URL = os.environ.get(
    "QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
)

# 2. 路径配置
BASE_PHOTO_PATH = "/app"                # 照片根目录
OUTPUT_DIR = "/app/Selected_Gallery"    # 结果输出目录
GRPC_SERVER = "localhost:50051"         # VLM Server 地址

# 3. 审美权重 (情, 光, 主, 构, 能, 技) - 侧重情绪和光影
WEIGHTS = np.array([0.3, 0.25, 0.1, 0.15, 0.1, 0.1])
dims = ["情绪", "光影", "主体", "构图", "能量", "技术"]
current_dir = os.path.dirname(os.path.abspath(__file__))
# ==========================================

client = OpenAI(api_key=QWEN_API_KEY, base_url=QWEN_BASE_URL) if QWEN_API_KEY else None

def get_qwen_critique(filename, scores):
    """调用千问生成专业且感性的点评"""
    if client is None:
        return "点评生成失败: 未配置 DASHSCOPE_API_KEY 环境变量"
    prompt = (
        f"照片名: {filename}\n"
        f"评分(0-10): 情绪={scores[0]}, 光影={scores[1]}, 主体={scores[2]}, "
        f"构图={scores[3]}, 能量={scores[4]}, 技术={scores[5]}\n\n"
        "请作为一名沉迷后摇音乐的摄影师，给出一段50字内的感性点评，"
        "并针对得分最低的项目给出一个具体的后期建议。"
    )

    low_dim = dims[np.argmin(scores)]

    prompt = (
        f"场景：Livehouse 现场。照片：{filename}。\n"
        f"这组照片的生命体征如下：{dict(zip(dims, scores))}\n\n"
        f"作为摄影师，你刚洗完这张片子。别说废话，别堆砌乐队名词。\n"
        f"如果{low_dim}这一项让你觉得这张图还没‘醒’过来，你会怎么在暗房里救它？\n"
        "用一种冷淡、私密、不超过40字的语气告诉我。哪怕是吐槽也可以。"
    )
    try:
        completion = client.chat.completions.create(
            model="qwen-plus",
            messages=[
                {"role": "system", "content": "你是一位网名为'慢慢'的独立摄影评论人，语言风格冷峻、专业且精准。"},
                {"role": "user", "content": prompt}
            ]
        )
        return completion.choices[0].message.content
    except Exception as e:
        return f"点评生成失败: {e}"

def organize_photo(filename, full_path, scores, total_score):
    """根据分数自动分类并移动文件"""
    if scores[0] > 7.0: # 情绪大过天
        subfolder = "High_Emotion_Atmosphere"
    elif total_score > 4.2:
        subfolder = "Technical_Masterpiece"
    else:
        return None

    target_path = os.path.join(OUTPUT_DIR, subfolder)
    os.makedirs(target_path, exist_ok=True)
    shutil.copy(full_path, os.path.join(target_path, filename))
    return subfolder

def run_culler():
    print(f"🚀 LumaKernel 启动 | 扫描根目录: {BASE_PHOTO_PATH}")
    
    # 建立 gRPC 连接
    channel = grpc.insecure_channel(GRPC_SERVER)
    stub = vision_pb2_grpc.VisionServiceStub(channel)

    # 扫描子目录
    sub_folders = ['bad', 'good', 'normal']
    all_photos = []
    for sub in sub_folders:
        folder_path = os.path.join(BASE_PHOTO_PATH, sub)
        if os.path.exists(folder_path):
            files = [f for f in os.listdir(folder_path) if f.lower().endswith(('.jpg', '.jpeg', '.png'))]
            for f in files:
                all_photos.append((f, os.path.join(folder_path, f)))

    if not all_photos:
        print("❌ 未在 /app 子目录下找到任何图片，请检查挂载路径。")
        return

    print(f"{'文件名':<18} | {'总分':<5} | {'Agent 决策'}")
    print("-" * 70)

    web_data_list = []

    for filename, full_path in all_photos:
        try:
            # 1. 构造请求：直接传路径，不读 bytes
            request = vision_pb2.AnalysisRequest(image_path=full_path)
            response = stub.AnalyzeImage(request)

            # 2. 🚨 暴力兜底逻辑：直接解析 reason 文本里的数字 🚨
            import re
            # 抓取 reason 里的所有数字（支持 0.17 这种小数）
            all_nums = re.findall(r'\d+\.\d+|\d+', response.reason)
            v = [float(n) for n in all_nums if 0 <= float(n) <= 10]

            # 3. 映射逻辑
            if len(v) >= 6:
                # 如果 AI 吐了 6 个数，直接按顺序取
                score_list = v[:6]
            elif len(v) >= 4:
                # 兼容你 Log 里的 4 个数情况，手动补齐 6 维
                score_list = [v[0], v[1], v[2], v[3], 5.0, 5.0]
            else:
                # 最后的保底，防止程序崩溃
                score_list = [5.0] * 6

            # 4. 统一放大 10 倍（因为 Log 显示 Server 吐的是 0-1 之间）
            score_list = [s * 10 if 0 < s <= 1.0 else s for s in score_list]
            
            # 5. 计算总分并输出
            total_score = np.dot(score_list, WEIGHTS)
            

            #print(f"DEBUG {filename}: emo={response.emotion}, light={response.lighting}, tech={response.technical}")
            
            # 4. 计算加权得分
            total_score = np.dot(score_list, WEIGHTS)
            
            # 5. 自动归档与点评（保持原样）
            category = organize_photo(filename, full_path, score_list, total_score)
            
            critique = get_qwen_critique(filename, score_list)
            
            cat_display = category if category else "Skip"
            print(f"{filename:<18} | {total_score:<5.2f} | {cat_display}")
            if critique:
                print(f"   └─ 【慢慢的笔记】: {critique}")

            # 🚨 新增：构造 JSON 数据
            photo_data = {
                "filename": filename,
                "total_score": round(total_score, 2),
                "critique": critique,
                # 将 6 个维度得分构造成字典
                "scores": {dims[i]: round(score_list[i], 1) for i in range(6)}
            }
            web_data_list.append(photo_data)

        except Exception as e:
            print(f"⚠️ 处理 {filename} 时出错: {e}")# 🚨 新增：导出数据到 JSON 文件

    output_json = os.path.join(current_dir, "luma_results.json")
    with open(output_json, 'w', encoding='utf-8') as f:
        json.dump(web_data_list, f, ensure_ascii=False, indent=2)
    
    print("-" * 70)
    print(f"✅ 完成！已将数据导出至: {output_json}")
    print(f"请将 /app 目录下的照片复制到该 JSON 所在的目录下，然后打开配套的 HTML 即可。")



if __name__ == "__main__":
    run_culler()