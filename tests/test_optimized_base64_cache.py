"""Decode cache for VLM base64 encoding: fast/full passes share one decode, bounded + safe."""
import base64
import io

from PIL import Image

from engine.operators import image_processor as ip
from engine.operators.image_processor import ImageProcessor


def _longest_side(b64: str) -> int:
    return max(Image.open(io.BytesIO(base64.b64decode(b64))).size)


def _count_decodes(monkeypatch):
    calls = {"n": 0}
    orig = ImageProcessor._decode_oriented_rgb

    def counting(path):
        calls["n"] += 1
        return orig(path)

    monkeypatch.setattr(ImageProcessor, "_decode_oriented_rgb", staticmethod(counting))
    return calls


def test_fast_and_full_share_single_decode(tmp_path, monkeypatch):
    p = tmp_path / "frame.jpg"
    Image.new("RGB", (2000, 1300), (120, 80, 40)).save(p, "JPEG", quality=90)
    ip._oriented_img_cache.clear()
    calls = _count_decodes(monkeypatch)

    b_full = ImageProcessor.get_optimized_base64(str(p), max_size=(1280, 1280))
    b_fast = ImageProcessor.get_optimized_base64(str(p), max_size=(896, 896))
    b_full_again = ImageProcessor.get_optimized_base64(str(p), max_size=(1280, 1280))

    assert calls["n"] == 1  # one decode shared across both sizes + the retry
    assert _longest_side(b_full) == 1280
    assert _longest_side(b_fast) == 896
    assert _longest_side(b_full_again) == 1280


def test_request_above_cache_cap_bypasses_and_preserves_resolution(tmp_path, monkeypatch):
    p = tmp_path / "frame.jpg"
    Image.new("RGB", (2000, 1300), (10, 20, 30)).save(p, "JPEG", quality=90)
    ip._oriented_img_cache.clear()
    calls = _count_decodes(monkeypatch)

    b_big = ImageProcessor.get_optimized_base64(str(p), max_size=(2048, 2048))
    # Larger than the cache cap → fresh decode, original resolution preserved (no upscale clamp).
    assert calls["n"] == 1
    assert _longest_side(b_big) == 2000


def test_mtime_change_invalidates_cache(tmp_path, monkeypatch):
    p = tmp_path / "frame.jpg"
    Image.new("RGB", (1200, 800), (0, 0, 0)).save(p, "JPEG")
    ip._oriented_img_cache.clear()
    calls = _count_decodes(monkeypatch)

    ImageProcessor.get_optimized_base64(str(p), max_size=(1024, 1024))
    assert calls["n"] == 1
    # Rewrite the file (new content/size/mtime) → must re-decode, not serve stale pixels.
    Image.new("RGB", (900, 600), (255, 255, 255)).save(p, "JPEG")
    ImageProcessor.get_optimized_base64(str(p), max_size=(1024, 1024))
    assert calls["n"] == 2


def test_cached_entry_not_mutated_by_caller(tmp_path):
    p = tmp_path / "frame.jpg"
    Image.new("RGB", (1500, 1000), (40, 40, 40)).save(p, "JPEG")
    ip._oriented_img_cache.clear()

    ImageProcessor.get_optimized_base64(str(p), max_size=(300, 300))
    # The shared cached image must keep its capped size, not shrink to the caller's 300px.
    key = next(iter(ip._oriented_img_cache))
    assert max(ip._oriented_img_cache[key].size) > 300


def test_lru_eviction_bounds_memory(tmp_path):
    ip._oriented_img_cache.clear()
    cap = ip._ORIENTED_CACHE_MAX_ENTRIES
    for i in range(cap + 3):
        q = tmp_path / f"f{i}.jpg"
        Image.new("RGB", (800, 600), (i % 255, 0, 0)).save(q, "JPEG")
        ImageProcessor.get_optimized_base64(str(q), max_size=(512, 512))
    assert len(ip._oriented_img_cache) <= cap
