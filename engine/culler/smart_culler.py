"""Smart image culling with two-stage filtering"""
import logging
from pathlib import Path
from typing import List, Tuple, Dict, Any
from engine.operators.image_processor import ImageProcessor


logger = logging.getLogger(__name__)


class SmartCuller:
    """
    Two-stage filtering for photo culling:
    Stage 1: OpenCV strict quality filtering
    Stage 2: Fast aesthetic assessment
    """
    
    def __init__(self, 
                 quality_thresholds: Dict = None,
                 fast_aesthetic_thresholds: Dict = None,
                 top_k: int = 100):
        """
        Initialize smart culler
        
        Args:
            quality_thresholds: Quality threshold configuration
            fast_aesthetic_thresholds: Fast aesthetic threshold configuration
            top_k: Maximum number of candidates to select
        """
        self.quality_thresholds = quality_thresholds or {}
        self.fast_aesthetic_thresholds = fast_aesthetic_thresholds or {}
        self.top_k = top_k
        
        # Statistics
        self.stats = {
            "total": 0,
            "stage1_rejected": 0,
            "stage2_rejected": 0,
            "candidates": 0
        }
    
    def select_top_candidates(self, 
                             image_paths: List[str],
                             skip_stage1: bool = False) -> List[Tuple[Path, Dict]]:
        """
        Select top candidates using two-stage filtering
        
        Stage 1: Remove obvious technical problems (blur, exposure, contrast)
        Stage 2: Fast aesthetic filtering on remaining images
        
        Args:
            image_paths: List of image file paths
            skip_stage1: If True, skip OpenCV filtering
            
        Returns:
            List of (Path, debug_info) tuples for selected candidates
        """
        self.stats["total"] = len(image_paths)
        candidates = []
        
        for img_path in image_paths:
            img_path = Path(img_path)
            
            # Stage 1: OpenCV quality check
            if not skip_stage1:
                passes_quality, reason, tech_score, debug_info = ImageProcessor.assess_image_quality(
                    str(img_path), 
                    self.quality_thresholds
                )
                
                if not passes_quality:
                    self.stats["stage1_rejected"] += 1
                    logger.debug(f"Stage 1 rejected {img_path.name}: {reason}")
                    continue
            else:
                tech_score = 70
                debug_info = {}
            
            # Stage 2: Fast aesthetic assessment
            fast_score = ImageProcessor.fast_aesthetic_assessment(str(img_path))
            
            tech_score_threshold = self.fast_aesthetic_thresholds.get("tech_score_min", 25)
            fast_score_threshold = self.fast_aesthetic_thresholds.get("fast_aesthetic_score_min", 15)
            
            if tech_score < tech_score_threshold or fast_score < fast_score_threshold:
                self.stats["stage2_rejected"] += 1
                logger.debug(f"Stage 2 rejected {img_path.name}: tech={tech_score:.0f}, aesthetic={fast_score:.0f}")
                continue
            
            # Passed both stages
            debug_info["tech_score"] = tech_score
            debug_info["fast_aesthetic_score"] = fast_score
            candidates.append((img_path, debug_info))
        
        # Sort by combined score and select top_k
        candidates.sort(key=lambda x: (x[1].get("tech_score", 0) + x[1].get("fast_aesthetic_score", 0)) / 2, 
                       reverse=True)
        
        selected = candidates[:self.top_k]
        self.stats["candidates"] = len(selected)
        
        logger.info(f"Culling complete: {self.stats['stage1_rejected']} rejected in stage 1, "
                   f"{self.stats['stage2_rejected']} rejected in stage 2, "
                   f"{self.stats['candidates']} candidates selected")
        
        return selected
    
    def get_stats(self) -> Dict[str, Any]:
        """Get culling statistics"""
        return self.stats.copy()