import time
import logging
from functools import wraps

from utils.logging_setup import configure_logging

configure_logging()
logger = logging.getLogger("LumaKernel.Timer")

def timer_decorator(func):
    """
    工业级耗时统计装饰器
    1. 使用 functools.wraps 保留原函数元数据（这对调试和生成文档很重要）
    2. 使用 logging 代替 print
    3. 采用 perf_counter 提供微秒级精度
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        # 记录开始时间
        start_time = time.perf_counter()
        
        try:
            # 执行业务逻辑
            result = func(*args, **kwargs)
            return result
        finally:
            # 无论成功还是报错，都要记录耗时
            end_time = time.perf_counter()
            duration = end_time - start_time
            
            # 关键：根据耗时长短自动调整日志级别
            # 比如：推理超过 2 秒，可以用 WARNING 提醒
            if duration > 2.0:
                logger.warning(f"Task [{func.__name__}] is SLOW: {duration:.4f}s")
            else:
                logger.info(f"Task [{func.__name__}] finished in {duration:.4f}s")
                
    return wrapper