from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

@unittest.skipUnless(importlib.util.find_spec("celery") is not None, "celery is not installed")
class GalleryExportBackgroundTests(unittest.TestCase):
    def test_background_request_enqueues_with_fixed_base_dir(self):
        from api.gallery_routes import ExportRequest, export_images

        task = MagicMock(id="export-task-1")
        req = ExportRequest(
            items=[{"file": "DSC0001.jpg", "rotate": 0}],
            background=True,
        )
        with (
            patch("api.gallery_routes._runtime_base_dir", return_value="/tmp/session/Previews"),
            patch("api.gallery_routes.celery_client.send_task", return_value=task) as send,
        ):
            response = export_images(req)

        self.assertEqual(response.status_code, 202)
        payload = json.loads(response.body)
        self.assertEqual(payload["task_id"], "export-task-1")
        self.assertEqual(payload["total"], 1)
        send.assert_called_once()
        self.assertEqual(send.call_args.kwargs["kwargs"]["base_dir"], "/tmp/session/Previews")

    def test_apfs_clone_falls_back_to_copy2(self):
        from api.gallery_routes import _copy_raw_with_apfs_clone

        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "source.ARW"
            dest = Path(tmp) / "dest.ARW"
            src.write_bytes(b"raw")
            with (
                patch(
                    "api.gallery_routes.subprocess.run",
                    side_effect=subprocess.CalledProcessError(1, ["/bin/cp"]),
                ),
                patch("api.gallery_routes.shutil.copy2") as copy2,
            ):
                mode = _copy_raw_with_apfs_clone(src, dest)

        self.assertEqual(mode, "copy")
        copy2.assert_called_once_with(src, dest)

    def test_prewarm_populates_both_caches_without_export_folder(self):
        from api.gallery_routes import ExportRequest, _export_images_impl

        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            previews = session / "Previews"
            raw_dir = session / "RAW"
            previews.mkdir(parents=True)
            raw_dir.mkdir()
            (previews / "DSC0001.jpg").write_bytes(b"jpeg")
            (raw_dir / "DSC0001.ARW").write_bytes(b"raw")
            cached = Path(tmp) / "cached.jpg"
            cached.write_bytes(b"cached")
            req = ExportRequest(items=[{"file": "DSC0001.jpg"}])

            with (
                patch(
                    "api.gallery_routes._export_processing_opts",
                    return_value={
                        "export_film_from_raw": True,
                        "export_film_jpeg_max_side": 3200,
                        "export_film_raw_max_side": 3200,
                    },
                ),
                patch("api.gallery_routes.path_allowed_for_film_render", return_value=True),
                patch("api.gallery_routes.render_film_to_cache", return_value=cached) as render,
            ):
                result = _export_images_impl(
                    req,
                    base_dir=str(previews),
                    prewarm_only=True,
                )

            self.assertTrue(result["success"])
            self.assertTrue(result["prewarm_only"])
            self.assertFalse((session / "exported_images").exists())
            self.assertEqual(render.call_count, 2)
            self.assertTrue(
                any(call.kwargs.get("raw_half_size") is True for call in render.call_args_list)
            )


if __name__ == "__main__":
    unittest.main()
