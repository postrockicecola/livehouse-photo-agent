import os
import json
from dataclasses import dataclass
from typing import List

@dataclass
class SelectionResult:
    filename: str
    score: float
    reason: str
    suggested_skill: str

class VLMPhotoCuller:
    def __init__(self, model_endpoint="http://localhost:11434/api/generate"):
        self.endpoint = model_endpoint

    def analyze_photo(self, img_path: str) -> SelectionResult:
        """调用本地 VLM 模型 (如 LLaVA/Qwen-VL) 进行审美评价"""
        # 这里写你调用本地模型 API 的逻辑
        # Prompt 建议：专注于光影、主体清晰度和构图
        prompt = "Evaluate this photo's aesthetic quality for a livehouse performance..."
        
        # 模拟模型返回的 JSON
        # response = self.call_vlm(img_path, prompt)
        return SelectionResult(
            filename=os.path.basename(img_path),
            score=85.0,
            reason="Excellent focus on the singer with dramatic backlighting.",
            suggested_skill="fuji_chrome"
        )

    def cull_best_shots(self, folder_path: str, top_k=3) -> List[SelectionResult]:
        """遍历文件夹，选出前 K 张最好的照片"""
        results = []
        for img in os.listdir(folder_path):
            if img.endswith(".jpg"):
                res = self.analyze_photo(os.path.join(folder_path, img))
                results.append(res)
        
        # 按分数排序
        results.sort(key=lambda x: x.score, reverse=True)
        return results[:top_k]