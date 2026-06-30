"""Configuration loader for livehouse.yaml"""
import yaml
import os
from pathlib import Path
from typing import Dict, Any

from utils.stage3_dimensions import STAGE3_DIM_KEYS


def default_config_path() -> str:
    """Config file for pipeline/API (override in Docker via ``LIVEHOUSE_CONFIG``)."""
    raw = os.environ.get("LIVEHOUSE_CONFIG", "configs/livehouse.yaml").strip()
    return raw or "configs/livehouse.yaml"


class ConfigLoader:
    """Load and validate configuration from livehouse.yaml"""
    
    DEFAULT_CONFIG_PATH = "configs/livehouse.yaml"
    
    # Default values when config is missing
    DEFAULTS = {
        "paths": {
            "source_dir": "./data/livehouse_session/Previews",
            "work_dir": "./data/livehouse_session",
            "folders": {
                "best": "AI_Best_90+",
                "keep": "AI_Keep_60-90",
                "trash": "AI_Trash_Below60"
            },
            "log_file": "aesthetic_audit.jsonl",
            "progress_file": ".processing_progress.json",
            "detailed_audit": "detailed_audit.txt",
            "selected_folder": "AI_Selected_Final",
            "manual_selected_folder": "manual_selected"
        },
        "quality_thresholds": {
            "laplacian_variance_min": 30,
            "laplacian_variance_slight_blur": 50,
            "laplacian_variance_medium": 120,
            "laplacian_variance_high": 200,
            "motion_blur_ratio_threshold": 1.5,
            "overexposed_threshold": 0.25,
            "overexposed_penalty": 0.15,
            "underexposed_threshold": 0.50,
            "underexposed_penalty": 0.35,
            "contrast_min": 10,
            "contrast_penalty": 20,
            "brightness_min": 5,
            "brightness_max": 250,
            "edge_ratio_min": 0.005
        },
        "fast_aesthetic": {
            "tech_score_min": 25,
            "fast_aesthetic_score_min": 15
        },
        "model": {
            "provider": "ollama",
            "endpoint": "http://localhost:11434",
            "model_name": "llava",
            "timeout": 300,
            "temperature": 0.8,
            "num_predict": 512,
            "max_retries": 3,
            "retry_delay": 1.0,
            "queue_wait_timeout_seconds": 60,
            "fallback_model_name": "",
            "fallback_num_predict": None,
            "max_concurrent_requests": 2,
            "max_inference_queue_size": 2,
            "inference_batch_size": 1,
            "inference_hard_timeout_seconds": 120,
        },
        "evaluation": {
            "default_weights": {
                "focus_sharpness": 0.125,
                "exposure_control": 0.125,
                "noise_cleanliness": 0.125,
                "composition_framing": 0.125,
                "light_color_character": 0.125,
                "moment_peak": 0.125,
                "atmosphere_impact": 0.125,
                "deliverable_subject": 0.125,
            },
            "artistic_motion_blur_weights": {
                "focus_sharpness": 0.07,
                "exposure_control": 0.10,
                "noise_cleanliness": 0.08,
                "composition_framing": 0.12,
                "light_color_character": 0.13,
                "moment_peak": 0.18,
                "atmosphere_impact": 0.22,
                "deliverable_subject": 0.10,
            },
            "ai_weight": 0.80,
            "technical_weight": 0.20
        },
        "classification": {
            "best_threshold": 90,
            "keep_threshold": 60,
            "selected_threshold": 70
        },
        "processing": {
            "max_workers": 2,
            "enable_checkpoint": True,
            "export_film_from_raw": True,
            "export_film_jpeg_max_side": 3200,
            "export_film_raw_max_side": 3200,
            "vibe_film": {
                "enabled": True,
                "llm_on_miss": True,
            },
        }
    }
    
    @classmethod
    def load(cls, config_path: str = None) -> Dict[str, Any]:
        """
        Load configuration from YAML file with fallback to defaults
        
        Args:
            config_path: Path to livehouse.yaml. If None, uses DEFAULT_CONFIG_PATH
            
        Returns:
            Merged configuration dictionary
        """
        if config_path is None:
            config_path = default_config_path()
        
        config_path = Path(config_path)
        
        # Load from file if exists
        if config_path.exists():
            with open(config_path, 'r', encoding='utf-8') as f:
                file_config = yaml.safe_load(f) or {}
        else:
            file_config = {}
        
        # Deep merge with defaults
        merged = cls._deep_merge(cls.DEFAULTS, file_config)

        ollama_host = os.environ.get("OLLAMA_HOST", "").strip()
        if ollama_host and isinstance(merged.get("model"), dict):
            merged["model"]["endpoint"] = ollama_host

        return merged
    
    @classmethod
    def _deep_merge(cls, base: Dict, override: Dict) -> Dict:
        """Deep merge override dict into base dict"""
        result = base.copy()
        
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = cls._deep_merge(result[key], value)
            else:
                result[key] = value
        
        return result
    
    @classmethod
    def get_quality_thresholds(cls, config: Dict) -> Dict[str, Any]:
        """Extract quality threshold settings"""
        return config.get("quality_thresholds", cls.DEFAULTS["quality_thresholds"])
    
    @classmethod
    def get_fast_aesthetic_thresholds(cls, config: Dict) -> Dict[str, Any]:
        """Extract fast aesthetic threshold settings"""
        return config.get("fast_aesthetic", cls.DEFAULTS["fast_aesthetic"])
    
    @classmethod
    def get_model_config(cls, config: Dict) -> Dict[str, Any]:
        """Extract model configuration"""
        return config.get("model", cls.DEFAULTS["model"])
    
    @classmethod
    def get_evaluation_weights(cls, config: Dict, blur_type: str = None) -> Dict[str, float]:
        """
        Get evaluation weights based on blur type
        
        Args:
            config: Configuration dictionary
            blur_type: One of 'artistic_motion_blur', 'motion_blur', or None for default
            
        Returns:
            Dictionary of dimension weights
        """
        eval_config = config.get("evaluation", cls.DEFAULTS["evaluation"])
        if blur_type == "artistic_motion_blur":
            raw = eval_config.get(
                "artistic_motion_blur_weights",
                cls.DEFAULTS["evaluation"]["artistic_motion_blur_weights"],
            )
        else:
            raw = eval_config.get("default_weights", cls.DEFAULTS["evaluation"]["default_weights"])

        defaults = (
            cls.DEFAULTS["evaluation"]["artistic_motion_blur_weights"]
            if blur_type == "artistic_motion_blur"
            else cls.DEFAULTS["evaluation"]["default_weights"]
        )
        out: Dict[str, float] = {}
        for dim in STAGE3_DIM_KEYS:
            try:
                out[dim] = float(raw.get(dim, defaults.get(dim, 0.0)))
            except (TypeError, ValueError):
                out[dim] = float(defaults.get(dim, 0.0))
        total = sum(out.values())
        if total <= 0:
            n = len(STAGE3_DIM_KEYS)
            return {d: 1.0 / n for d in STAGE3_DIM_KEYS}
        return {d: out[d] / total for d in STAGE3_DIM_KEYS}
    
    @classmethod
    def get_classification_thresholds(cls, config: Dict) -> Dict[str, float]:
        """Extract classification threshold settings"""
        return config.get("classification", cls.DEFAULTS["classification"])
    
    @classmethod
    def get_folder_paths(cls, config: Dict, base_dir: Path = None) -> Dict[str, Path]:
        """
        Get full paths for all folders
        
        Args:
            config: Configuration dictionary
            base_dir: Base directory for relative paths. If None, uses source_dir as base
            
        Returns:
            Dictionary of absolute folder paths
        """
        if base_dir is None:
            base_dir = Path(config["paths"]["source_dir"])
        else:
            base_dir = Path(base_dir)
        
        paths_config = config["paths"]
        folders_config = paths_config.get("folders", cls.DEFAULTS["paths"]["folders"])
        
        result = {}
        for key, folder_name in folders_config.items():
            result[key] = base_dir / folder_name
        
        return result
    
    @classmethod
    def get_log_paths(cls, config: Dict, base_dir: Path = None) -> Dict[str, Path]:
        """
        Get full paths for all log files
        
        Args:
            config: Configuration dictionary
            base_dir: Base directory for relative paths. If None, uses source_dir as base
            
        Returns:
            Dictionary of absolute log file paths
        """
        if base_dir is None:
            base_dir = Path(config["paths"]["source_dir"])
        else:
            base_dir = Path(base_dir)
        
        paths_config = config["paths"]
        
        return {
            "log_file": base_dir / paths_config.get("log_file", cls.DEFAULTS["paths"]["log_file"]),
            "progress_file": base_dir / paths_config.get("progress_file", cls.DEFAULTS["paths"]["progress_file"]),
            "detailed_audit": base_dir / paths_config.get("detailed_audit", cls.DEFAULTS["paths"]["detailed_audit"]),
            "selected_folder": base_dir / paths_config.get("selected_folder", cls.DEFAULTS["paths"]["selected_folder"]),
            "manual_selected_folder": base_dir / paths_config.get("manual_selected_folder", cls.DEFAULTS["paths"]["manual_selected_folder"])
        }
