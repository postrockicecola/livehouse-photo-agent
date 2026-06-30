"""resolve_capture_rotation_degrees: RAW-sibling rotation fallback for strip-exported previews."""
from __future__ import annotations

from PIL import Image

from services import jpeg_exif_orientation as jeo


def _write_jpeg(path, size=(40, 20), exif_orientation: int | None = None) -> None:
    im = Image.new("RGB", size, (30, 30, 30))
    kwargs = {}
    if exif_orientation is not None:
        exif = Image.Exif()
        exif[274] = exif_orientation
        kwargs["exif"] = exif
    im.save(path, "JPEG", **kwargs)


def setup_function(_fn):
    jeo._find_raw_sibling.cache_clear()
    jeo._raw_orientation_degrees.cache_clear()


def test_no_raw_sibling_returns_zero(tmp_path):
    jpg = tmp_path / "DSC0001.jpg"
    _write_jpeg(jpg)
    assert jeo.resolve_capture_rotation_degrees(jpg) == 0


def test_jpeg_with_own_exif_orientation_returns_zero(tmp_path):
    # EXIF 6 present → exif_transpose handles it; RAW fallback must not double-rotate.
    (tmp_path / "RAW").mkdir()
    (tmp_path / "RAW" / "DSC0002.ARW").write_bytes(b"fake raw")
    jpg = tmp_path / "DSC0002.jpg"
    _write_jpeg(jpg, exif_orientation=6)
    assert jeo.resolve_capture_rotation_degrees(jpg) == 0


def test_raw_sibling_orientation_applied(tmp_path, monkeypatch):
    session = tmp_path / "2026-01-30"
    (session / "RAW").mkdir(parents=True)
    (session / "Previews").mkdir()
    raw = session / "RAW" / "DSC0003.ARW"
    raw.write_bytes(b"fake raw")
    jpg = session / "Previews" / "DSC0003.jpg"
    _write_jpeg(jpg)

    monkeypatch.setattr(
        jeo, "_orientation_from_exiftool", lambda p: 8 if str(p).lower().endswith(".arw") else None
    )
    assert jeo.resolve_capture_rotation_degrees(jpg) == -90


def test_raw_sibling_found_through_nested_preview_dirs(tmp_path, monkeypatch):
    # Previews/AI_Keep_60-90/DSC0004.jpg → session/RAW/DSC0004.ARW (2 levels up)
    session = tmp_path / "2026-03-27"
    (session / "RAW").mkdir(parents=True)
    nested = session / "Previews" / "AI_Keep_60-90"
    nested.mkdir(parents=True)
    (session / "RAW" / "DSC0004.ARW").write_bytes(b"fake raw")
    jpg = nested / "DSC0004.jpg"
    _write_jpeg(jpg)

    monkeypatch.setattr(
        jeo, "_orientation_from_exiftool", lambda p: 6 if str(p).lower().endswith(".arw") else None
    )
    assert jeo.resolve_capture_rotation_degrees(jpg) == 90
