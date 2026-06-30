import os
import sys
import time
import logging
import cv2
import base64
import requests
import re
import gc
from concurrent import futures

# --- 路径处理 ---
current_dir = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(current_dir, "../../"))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import grpc
from api.gen.python import vision_pb2, vision_pb2_grpc

from utils.logging_setup import configure_logging

configure_logging()

# --- 配置区 ---
# 如果是在 Docker 内部访问宿主机的 Ollama，请确保这个地址可用
OLLAMA_URL = "http://host.docker.internal:11434/api/generate"
MODEL_NAME = "moondream"
# M4 建议并发控制在 4-6 之间，以保证单个推理任务的 NPU 带宽
MAX_WORKERS = 6

logger = logging.getLogger("VLM-Server")

class VisionServicer(vision_pb2_grpc.VisionServiceServicer):
    
    def AnalyzeImage(self, request, context):
        """gRPC 接口实现"""
        img_path = request.image_path
        # 允许客户端传入自定义 Prompt，如果没有则使用内置的高强度 Prompt
        prompt = request.prompt if request.prompt else "Livehouse photography evaluation"
        
        logger.info(f"📸 正在处理: {os.path.basename(img_path)}")
        
        try:
            result = self._run_inference(img_path, prompt)
            
            # 记录 AI 的原始吐槽，方便大鱼你后续调优
            logger.info(f"🤖 AI 分析结果: {result.get('reason').strip()}")
            
            return vision_pb2.AestheticScores(
                composition=result.get("comp", 0.0),
                energy=result.get("energy", 0.0),
                technical=result.get("tech", 0.0),
                total_score=result.get("total", 0.0),
                reason=result.get("reason", "Success")
            )
        except Exception as e:
            logger.error(f"❌ 推理异常: {str(e)}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return vision_pb2.AestheticScores()
        finally:
            # 强制清理内存碎片，防止 M4 长时间运行后的内存波动
            gc.collect()

    def _run_inference(self, img_path, prompt):
        """内部推理逻辑"""
        # 1. 图像读取与预处理
        img = cv2.imread(img_path)
        if img is None:
            raise FileNotFoundError(f"无法读取图片: {img_path}")

        # 稍微调大一点点尺寸，增加对焦判断的准确度
        h, w = img.shape[:2]
        max_side = 512 
        scale = max_side / max(h, w)
        img_resized = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)

        _, buffer = cv2.imencode('.jpg', img_resized, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
        img_b64 = base64.b64encode(buffer).decode('utf-8')

        # 2. 构造高强度评分 Prompt (针对那 14 张图的 Good/Bad 差异化优化)
# 修改 vlm_server.py 中的 system_prompt
        system_prompt = (
            "You are a CRITICAL Livehouse photography judge.\n"
            "Task: Evaluate this image based on 6 dimensions (0-10 scale):\n"
            "1. [Emotion]: Facial expressions & performer focus.\n"
            "2. [Lighting]: Use of colors, spotlights, and ambiance.\n"
            "3. [Subject]: Purity and clarity of the main performer vs background chaos.\n"
            "4. [Composition]: Dynamic lines and iconic framing.\n"
            "5. [Energy]: Iconic poses and dynamic movement.\n"
            "6. [Technicality]: Acceptance of atmospheric blur.\n"
            "Output: Scores: [Emo, Light, Sub, Comp, Ene, Tech].Brief critique."
        )

        payload = {
            "model": MODEL_NAME,
            "prompt": system_prompt,
            "images": [img_b64],
            "stream": False,
            "options": {
                "temperature": 0.1, # 降低随机性，让评分更稳
                "num_predict": 100,
                "top_p": 0.9
            }
        }

        response = requests.post(OLLAMA_URL, json=payload, timeout=60)
        if response.status_code != 200:
            raise ConnectionError(f"Ollama Error: {response.text}")

        ai_text = response.json().get("response", "")
        
        # 3. 解析结果
        return self._parse_enhanced_scores(ai_text)

    def _parse_enhanced_scores(self, text):
        """增强版正则解析：专门应对 AI 的各种啰嗦"""
        result = {"comp": 0.0, "energy": 0.0, "tech": 0.0, "total": 0.0, "reason": text}
        
        # 尝试匹配 [x, y, z] 格式
        pattern = re.findall(r'\[([\d\.]+),\s*([\d\.]+),\s*([\d\.]+)\]', text)
        
        try:
            if pattern:
                scores = [float(s) for s in pattern[0]]
                result["comp"], result["energy"], result["tech"] = scores
            else:
                # 备用方案：抓取前三个出现的数字
                all_numbers = re.findall(r'\d+\.?\d*', text)
                # 过滤掉 0-10 范围外的噪音
                valid_nums = [float(n) for n in all_numbers if 0.0 <= float(n) <= 10.0][:3]
                if len(valid_nums) >= 3:
                    result["comp"], result["energy"], result["tech"] = valid_nums
            
            # 归一化到 0-1.0 量级并计算总分
            #result["comp"] /= 10.0
            #result["energy"] /= 10.0
            #result["tech"] /= 10.0
            result["total"] = result["comp"] * 0.3 + result["energy"] * 0.5 + result["tech"] * 0.2
            
        except Exception as e:
            logger.warning(f"解析分数失败: {e} | 原文: {text}")
            
        return result

def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=MAX_WORKERS))
    vision_pb2_grpc.add_VisionServiceServicer_to_server(VisionServicer(), server)
    
    server.add_insecure_port('[::]:50051')
    logger.info("🚀 LumaKernel v2 Server 启动成功 | 监听端口: 50051")
    logger.info(f"M4 优化模式：并发 Worker = {MAX_WORKERS}")
    
    server.start()
    server.wait_for_termination()

if __name__ == '__main__':
    serve()