# agents/filters/semantic.py
import requests
import base64

def has_subject(image_path):
    """
    调用本地 Ollama 的 Moondream2 模型进行语义检查。
    """
    with open(image_path, "rb") as f:
        img_base64 = base64.b64encode(f.read()).decode('utf-8')

    payload = {
        "model": "moondream",
        "prompt": "Is there a visible musician, singer, or musical instrument in this photo? Answer with 'yes' or 'no' and a short reason.",
        "stream": False,
        "images": [img_base64]
    }

    try:
        response = requests.post("http://host.docker.internal:11434/api/generate", json=payload)
        text = response.json().get("response", "").lower()
        print(f"DEBUG: Moondream's answer for {image_path}: '{text}'")
        # 只要包含 yes，就认为通过语义初筛
        return "yes" in text, text
    except Exception as e:
        print(f"VLM Error: {e}")
        return True, "error_fallback" # 出错时默认保留，防止误删