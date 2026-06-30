# engine/operators/image_proc.py
import cv2

def calculate_energy_score(image_path):
    """计算单张图片的能量得分"""
    img = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if img is None: return 0
    img_small = cv2.resize(img, (512, 512))
    sharpness = cv2.Laplacian(img_small, cv2.CV_64F).var()
    contrast = img_small.std()
    return sharpness * (contrast ** 1.5)

def apply_clahe_enhancement(img):
    """CLAHE 增强逻辑"""
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    l = clahe.apply(l)
    return cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2BGR)