"""Shared image-format support for pipeline and gallery paths."""
from __future__ import annotations

from functools import lru_cache

SUPPORTED_IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".heic", ".heif"})


@lru_cache(maxsize=1)
def register_heif_opener() -> bool:
    """Register Pillow HEIF/HEIC decoding when pillow-heif is installed."""
    try:
        import pillow_heif  # type: ignore[import-not-found]

        pillow_heif.register_heif_opener()
        return True
    except ImportError:
        return False
