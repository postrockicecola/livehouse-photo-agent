# agents/culler_pipeline.py
from .filters import traditional, semantic

def run_culling_suite(image_path):
    print(f"🔍 Processing: {image_path}")
    
    # 1. 物理检查 (Blur & Exposure)
    is_sharp, v_score = traditional.check_blur(image_path)
    if not is_sharp:
        return False, f"Blurry (Score: {v_score:.2f})"
        
    is_exposed, b_score = traditional.check_exposure(image_path)
    if not is_exposed:
        return False, f"Bad Exposure (Avg: {b_score:.2f})"

    # 2. 语义检查 (Moondream)
    has_content, reason = semantic.has_subject(image_path)
    if not has_content:
        return False, f"No Subject: {reason}"

    return True, "Passed All Filters"