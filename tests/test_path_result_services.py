from __future__ import annotations

import json
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.path_service import PathResolver
from services.result_service import (
    load_gallery_page,
    load_results,
    merge_json_and_disk_gallery_rows,
)


class PathResolverTests(unittest.TestCase):
    def test_resolve_preview_from_classified_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            previews = Path(tmp)
            keep_dir = previews / "keep"
            keep_dir.mkdir(parents=True, exist_ok=True)
            target = keep_dir / "IMG_0001.jpg"
            target.write_bytes(b"jpeg")

            resolver = PathResolver(previews)
            resolved = resolver.resolve_preview("IMG_0001.jpg")

            self.assertEqual(resolved.resolve(), target.resolve())

    def test_resolve_raw_supports_latest_session_raw_hint(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "Archive"
            session = archive / "2026-01-01_001"
            previews = session / "Previews"
            raw_flat = archive / "flat_raw"
            runtime_dir = archive / "runtime"
            previews.mkdir(parents=True, exist_ok=True)
            raw_flat.mkdir(parents=True, exist_ok=True)
            runtime_dir.mkdir(parents=True, exist_ok=True)

            raw_file = raw_flat / "IMG_0002.ARW"
            raw_file.write_bytes(b"raw")
            latest_ref = {
                "previews_dir": str(previews.resolve()),
                "session_dir": str(session.resolve()),
                "raw_dir": str(raw_flat.resolve()),
            }
            (runtime_dir / "latest_session.json").write_text(
                json.dumps(latest_ref),
                encoding="utf-8",
            )

            resolver = PathResolver(previews)
            resolved = resolver.resolve_raw("IMG_0002.jpg")

            self.assertEqual(resolved.resolve(), raw_file.resolve())


class ResultServiceTests(unittest.TestCase):
    def test_load_results_normalizes_scores_and_resolves_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "Archive"
            session = archive / "session_a"
            previews = session / "Previews"
            runtime_dir = archive / "runtime"
            raw_dir = session / "RAW"
            previews.mkdir(parents=True, exist_ok=True)
            runtime_dir.mkdir(parents=True, exist_ok=True)
            raw_dir.mkdir(parents=True, exist_ok=True)

            image_name = "IMG_1001.jpg"
            raw_name = "IMG_1001.ARW"
            (raw_dir / raw_name).write_bytes(b"raw")
            keep_dir = previews / "AI_Keep_60-90"
            keep_dir.mkdir(parents=True, exist_ok=True)
            # Real JPEG (landscape): orientation sync opens it with PIL, and after
            # the patched 90° rotation the displayed frame must report as portrait.
            from PIL import Image

            Image.new("RGB", (20, 10), (40, 40, 40)).save(keep_dir / image_name, "JPEG")

            latest_ref = {
                "previews_dir": str(previews.resolve()),
                "session_dir": str(session.resolve()),
                "raw_dir": str(raw_dir.resolve()),
            }
            (runtime_dir / "latest_session.json").write_text(
                json.dumps(latest_ref),
                encoding="utf-8",
            )

            analysis = [
                {
                    "file": image_name,
                    "path": image_name,
                    "scores": {
                        "energy": "81.2",
                        "technical": "79",
                        "composition": 77.5,
                        "overall": "88.6",
                    },
                }
            ]
            (previews / "analysis_results.json").write_text(
                json.dumps(analysis),
                encoding="utf-8",
            )

            with patch("services.result_service._read_raw_orientation_degrees", return_value=90):
                items = load_results(str(previews))

            self.assertEqual(len(items), 1)
            item = items[0]
            self.assertEqual(item["energy"], 81.2)
            self.assertEqual(item["technical"], 79.0)
            self.assertEqual(item["composition"], 77.5)
            self.assertEqual(item["overall_score"], 88.6)
            self.assertTrue(item["path"].endswith(f"AI_Keep_60-90/{image_name}"))
            self.assertEqual(item["rotate_degrees"], 90)
            self.assertEqual(item["orientation"], "portrait")
            self.assertIn("algorithm_version", item)
            self.assertIn("path_quoted", item)
            self.assertIn("before_path_quoted", item)

    def test_load_gallery_page_returns_same_slice_as_full_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "Archive"
            session = archive / "session_a"
            previews = session / "Previews"
            runtime_dir = archive / "runtime"
            raw_dir = session / "RAW"
            previews.mkdir(parents=True, exist_ok=True)
            runtime_dir.mkdir(parents=True, exist_ok=True)
            raw_dir.mkdir(parents=True, exist_ok=True)

            image_name = "IMG_2002.jpg"
            raw_name = "IMG_2002.ARW"
            (raw_dir / raw_name).write_bytes(b"raw")
            keep_dir = previews / "AI_Keep_60-90"
            keep_dir.mkdir(parents=True, exist_ok=True)
            (keep_dir / image_name).write_bytes(b"jpeg")

            latest_ref = {
                "previews_dir": str(previews.resolve()),
                "session_dir": str(session.resolve()),
                "raw_dir": str(raw_dir.resolve()),
            }
            (runtime_dir / "latest_session.json").write_text(
                json.dumps(latest_ref),
                encoding="utf-8",
            )

            analysis = [
                {
                    "file": image_name,
                    "path": image_name,
                    "scores": {
                        "energy": "70",
                        "technical": "70",
                        "composition": 70,
                        "overall": "71",
                    },
                }
            ]
            (previews / "analysis_results.json").write_text(
                json.dumps(analysis),
                encoding="utf-8",
            )

            with patch("services.result_service._read_raw_orientation_degrees", return_value=-90):
                full = load_results(str(previews))
                sliced, total, start, end, has_more, _total_raw = load_gallery_page(
                    str(previews), "overall", 0, 50, lite=False, dedupe=False
                )

            self.assertEqual(total, 1)
            self.assertEqual(start, 0)
            self.assertEqual(end, 1)
            self.assertFalse(has_more)
            self.assertEqual(len(sliced), 1)
            self.assertEqual(sliced[0]["overall_score"], full[0]["overall_score"])
            self.assertEqual(sliced[0]["path"], full[0]["path"])
            self.assertEqual(sliced[0]["rotate_degrees"], full[0]["rotate_degrees"])

    def test_load_gallery_page_lite_skips_layout_and_orientation(self):
        with tempfile.TemporaryDirectory() as tmp:
            previews = Path(tmp)
            previews.mkdir(parents=True, exist_ok=True)
            analysis = [
                {
                    "file": "a.jpg",
                    "path": "a.jpg",
                    "scores": {"overall": 50, "energy": 5, "technical": 5, "composition": 5},
                }
            ]
            (previews / "analysis_results.json").write_text(
                json.dumps(analysis),
                encoding="utf-8",
            )
            (previews / "a.jpg").write_bytes(b"jpeg")

            with patch("services.result_service.inject_layout") as inj_lay, patch(
                "services.result_service.inject_orientation"
            ) as inj_ori:
                load_gallery_page(str(previews), "overall", 0, 10, lite=True)
                inj_lay.assert_not_called()
                inj_ori.assert_not_called()

    def test_load_gallery_page_full_enrichment_when_lite_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            previews = Path(tmp)
            previews.mkdir(parents=True, exist_ok=True)
            analysis = [
                {
                    "file": "a.jpg",
                    "path": "a.jpg",
                    "scores": {"overall": 50, "energy": 5, "technical": 5, "composition": 5},
                }
            ]
            (previews / "analysis_results.json").write_text(
                json.dumps(analysis),
                encoding="utf-8",
            )
            (previews / "a.jpg").write_bytes(b"jpeg")

            with patch("services.result_service.inject_layout") as inj_lay, patch(
                "services.result_service.inject_orientation"
            ) as inj_ori:
                load_gallery_page(str(previews), "overall", 0, 10, lite=False)
                self.assertEqual(inj_lay.call_count, 1)
                self.assertEqual(inj_ori.call_count, 1)

    def test_load_gallery_page_merges_disk_previews_while_json_partial(self):
        """Running session: scored JSON + still-unscored Previews should both appear."""
        with tempfile.TemporaryDirectory() as tmp:
            previews = Path(tmp)
            previews.mkdir(parents=True, exist_ok=True)
            (previews / "scored.jpg").write_bytes(b"jpeg")
            (previews / "pending.jpg").write_bytes(b"jpeg")
            (previews / "analysis_results.json").write_text(
                json.dumps(
                    [
                        {
                            "file": "scored.jpg",
                            "path": "scored.jpg",
                            "scores": {
                                "overall": 80,
                                "energy": 8,
                                "technical": 8,
                                "composition": 8,
                            },
                        }
                    ]
                ),
                encoding="utf-8",
            )
            sliced, total, _s, _e, _more, total_raw = load_gallery_page(
                str(previews), "overall", 0, 50, lite=True, dedupe=False
            )
            names = {str(r.get("file")) for r in sliced}
            self.assertEqual(total, 2)
            self.assertEqual(total_raw, 2)
            self.assertEqual(names, {"scored.jpg", "pending.jpg"})
            scored = next(r for r in sliced if r.get("file") == "scored.jpg")
            pending = next(r for r in sliced if r.get("file") == "pending.jpg")
            self.assertEqual(scored.get("overall_score"), 80.0)
            self.assertEqual(pending.get("overall_score"), 0.0)
            self.assertTrue(pending.get("analysis_pending"))

    def test_merge_json_and_disk_gallery_rows_prefers_json(self):
        json_rows = [{"file": "a.jpg", "overall_score": 90}]
        disk_rows = [
            {"file": "a.jpg", "overall_score": 0.0, "analysis_pending": True},
            {"file": "b.jpg", "overall_score": 0.0, "analysis_pending": True},
        ]
        merged = merge_json_and_disk_gallery_rows(json_rows, disk_rows)
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0]["overall_score"], 90)
        self.assertEqual(merged[1]["file"], "b.jpg")


@unittest.skipUnless(
    importlib.util.find_spec("fastapi") is not None,
    "fastapi is not installed in current environment",
)
class ImageApiRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from api.gallery_routes import router

        app = FastAPI()
        app.include_router(router)
        cls.client = TestClient(app)

    def test_image_route_returns_cached_file_when_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "source.jpg"
            cached = tmp_path / "cached.jpg"
            src.write_bytes(b"source-bytes")
            cached.write_bytes(b"cached-bytes")

            with patch("api.gallery_routes._runtime_base_dir", return_value=str(tmp_path)):
                with patch(
                    "api.gallery_routes.ImageService.build_cached_image",
                    return_value=cached,
                ) as build_mock:
                    resp = self.client.get("/image", params={"path": str(src)})

            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.content, b"cached-bytes")
            self.assertEqual(
                resp.headers.get("cache-control"),
                "public, max-age=31536000, immutable",
            )
            build_mock.assert_called_once()

    def test_image_route_falls_back_to_original_when_cache_build_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "source.jpg"
            src.write_bytes(b"source-only")

            with patch("api.gallery_routes._runtime_base_dir", return_value=str(tmp_path)):
                with patch(
                    "api.gallery_routes.ImageService.build_cached_image",
                    return_value=None,
                ) as build_mock:
                    resp = self.client.get("/image", params={"path": str(src)})

            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.content, b"source-only")
            build_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
