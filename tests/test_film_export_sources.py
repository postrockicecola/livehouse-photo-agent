from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from services.film_render_service import (
    EXPORT_DIR_GRADED_FROM_RAW,
    EXPORT_DIR_JPEG,
    EXPORT_DIR_RAW_COPY,
    is_raw_path,
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


if __name__ == "__main__":
    unittest.main()
