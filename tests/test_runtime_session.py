from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from utils.runtime_session import (
    read_newest_latest_session_pointer,
    write_latest_session_pointer,
)


class RuntimeSessionTests(unittest.TestCase):
    def test_write_latest_session_pointer(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "Archive"
            session = archive / "sess1"
            previews = session / "Previews"
            raw = session / "RAW"
            previews.mkdir(parents=True)
            raw.mkdir(parents=True)

            written = write_latest_session_pointer(previews)
            self.assertIsNotNone(written)
            ref_path = archive / "runtime" / "latest_session.json"
            self.assertEqual(written.resolve(), ref_path.resolve())

            data = json.loads(ref_path.read_text(encoding="utf-8"))
            self.assertEqual(data["previews_dir"], str(previews.resolve()))
            self.assertEqual(data["session_dir"], str(session.resolve()))
            self.assertEqual(data["raw_dir"], str(raw.resolve()))

    def test_read_newest_latest_session_pointer_picks_latest_mtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive_a = root / "ArchiveA"
            archive_b = root / "ArchiveB"
            prev_a = archive_a / "sess" / "Previews"
            prev_b = archive_b / "sess" / "Previews"
            prev_a.mkdir(parents=True)
            prev_b.mkdir(parents=True)

            write_latest_session_pointer(prev_a)
            time.sleep(0.05)
            write_latest_session_pointer(prev_b)

            class _FakeConn:
                def execute(self, _sql):
                    class _Cur:
                        def fetchall(self):
                            return [(str(archive_a),), (str(archive_b),)]

                    return _Cur()

                def close(self) -> None:
                    return None

            with patch("utils.luma_brain.brain_connect", return_value=_FakeConn()):
                with patch(
                    "utils.studio_ingest_config.read_ingest_config",
                    return_value={"archive_root": ""},
                ):
                    hit = read_newest_latest_session_pointer(base_dir=root)
            self.assertIsNotNone(hit)
            _refp, ref = hit
            self.assertEqual(ref["previews_dir"], str(prev_b.resolve()))


if __name__ == "__main__":
    unittest.main()
