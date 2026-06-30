"""Load JSONL audit files written by the aesthetic pipeline."""
import json
import logging
from pathlib import Path
from typing import Dict, Any

logger = logging.getLogger(__name__)


def load_audit_jsonl(audit_path: Path) -> Dict[str, Any]:
    """
    Load lines from aesthetic_audit.jsonl (or equivalent), keyed by filename.

    Supports keys: file_name, image, image_name, file.
    """
    audit_data: Dict[str, Any] = {}
    if not audit_path.exists():
        return audit_data

    try:
        with open(audit_path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                key = (
                    data.get("file_name")
                    or data.get("image")
                    or data.get("image_name")
                    or data.get("file")
                )
                if key:
                    audit_data[key] = data
    except OSError as e:
        logger.error("Error reading audit file %s: %s", audit_path, e)

    return audit_data
