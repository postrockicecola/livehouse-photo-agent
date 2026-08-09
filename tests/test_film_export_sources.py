from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

from services.film_render_service import (
    EXPORT_DIR_GRADED_FROM_RAW,
    EXPORT_DIR_JPEG,
    EXPORT_DIR_RAW_COPY,
    _cache_file_name,
    is_raw_path,
    load_rgb_u8,
    resolve_film_catalog_paths,
    resolve_film_sources_for_export,
)
from services.path_service import PathResolver


class FilmExportSourcesTests(unittest.TestCase):
    def _session_tree(self, tmp: str) -> tuple[Path, PathResolver]:
        archive = Path(tmp) / "Archive"
        session = archive / "2026-05-16"
        previews = session / "Previews"
        raw_dir = session / "RAW"
        runtime_dir = archive / "runtime"
        previews.mkdir(parents=True, exist_ok=True)
        raw_dir.mkdir(parents=True, exist_ok=True)
        runtime_dir.mkdir(parents=True, exist_ok=True)
        (previews / "DSC0001.jpg").write_bytes(b"jpeg")
        (raw_dir / "DSC0001.ARW").write_bytes(b"raw")
        (runtime_dir / "latest_session.json").write_text(
            json.dumps(
                {
                    "previews_dir": str(previews.resolve()),
                    "session_dir": str(session.resolve()),
                    "raw_dir": str(raw_dir.resolve()),
                }
            ),
            encoding="utf-8",
        )
        return previews, PathResolver(previews)

    def test_is_raw_path(self):
        self.assertTrue(is_raw_path(Path("a.ARW")))
        self.assertFalse(is_raw_path(Path("a.jpg")))

    def test_resolve_film_catalog_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            previews, resolver = self._session_tree(tmp)
            paths = resolve_film_catalog_paths(resolver, "DSC0001.jpg")
            self.assertTrue(paths["preview"].is_file())
            self.assertTrue(paths["raw"].is_file())
            self.assertIsNone(paths["explicit"])

    def test_resolve_film_sources_skips_raw(self):
        with tempfile.TemporaryDirectory() as tmp:
            previews, resolver = self._session_tree(tmp)
            raw_file = previews.parent / "RAW" / "DSC0001.ARW"
            sources = resolve_film_sources_for_export(
                resolver, "DSC0001.jpg", explicit_source=raw_file
            )
            self.assertEqual(len(sources), 1)
            self.assertEqual(sources[0][1], "preview")

    def test_export_dir_constants(self):
        self.assertEqual(EXPORT_DIR_JPEG, "jpeg")
        self.assertEqual(EXPORT_DIR_RAW_COPY, "raw")
        self.assertEqual(EXPORT_DIR_GRADED_FROM_RAW, "graded_from_raw")

    def test_raw_half_size_is_forwarded_and_changes_cache_key(self):
        raw_ctx = MagicMock()
        raw_ctx.__enter__.return_value.postprocess.return_value = np.zeros((2, 2, 3), dtype=np.uint8)
        fake_rawpy = MagicMock()
        fake_rawpy.imread.return_value = raw_ctx
        with patch.dict(sys.modules, {"rawpy": fake_rawpy}):
            out = load_rgb_u8(Path("DSC0001.ARW"), raw_half_size=True)

        self.assertEqual(out.shape, (2, 2, 3))
        raw_ctx.__enter__.return_value.postprocess.assert_called_once_with(
            use_camera_wb=True,
            no_auto_bright=False,
            half_size=True,
            output_bps=8,
        )
        full = _cache_file_name(Path("DSC0001.ARW"), "film_cinestill_800t", 0, 3200)
        half = _cache_file_name(
            Path("DSC0001.ARW"),
            "film_cinestill_800t",
            0,
            3200,
            raw_half_size=True,
        )
        self.assertNotEqual(full, half)


if __name__ == "__main__":
    unittest.main()
