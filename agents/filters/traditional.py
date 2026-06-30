# agents/filters/traditional.py
import cv2
import numpy as np

def check_blur(image_path, threshold=100.0):
    """
    使用拉普拉斯算子计算图像方差，评估清晰度。
    阈值需要根据你的 RAW 缩略图质量微调。
    """
    image = cv2.imread(image_path)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    variance = cv2.Laplacian(gray, cv2.CV_64F).var()
    return variance > threshold, variance

def check_exposure(image_path, low_thresh=5, high_thresh=250):
    """
    通过直方图判断是否死黑或过曝。
    """
    image = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
    avg_brightness = np.mean(image)
    # 如果平均亮度太低（死黑）或太高（过曝），视作废片
    is_ok = low_thresh < avg_brightness < high_thresh
    return is_ok, avg_brightness