"""Repository root resolution for scripts and generated launchers."""
from pathlib import Path


def repo_root() -> Path:
    """Return the Livehouse-Photography-Agent project root directory."""
    return Path(__file__).resolve().parents[1]
