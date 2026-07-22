"""Fast unit checks for the free-beta runtime foundation."""

from __future__ import annotations

import io
import json
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from mimir_core_v2_release_check import source_video_hashes

from .ego_vehicle import polygons_for_camera
from .output_writer import write_latest_session
from .progress import PROGRESS_PREFIX, PROGRESS_PROTOCOL_VERSION, ProgressReporter


class ReleaseFoundationTests(unittest.TestCase):
    def test_progress_protocol_has_real_work_and_eta(self) -> None:
        reporter = ProgressReporter("session-test", started_at=time.perf_counter() - 10.0)
        output = io.StringIO()
        with redirect_stdout(output):
            reporter.emit("detecting_activity", "Scanning 2 of 4", completed=2, total=4)
        line = output.getvalue().strip()
        self.assertTrue(line.startswith(PROGRESS_PREFIX + " "))
        payload = json.loads(line[len(PROGRESS_PREFIX) + 1 :])
        self.assertEqual(payload["protocol_version"], PROGRESS_PROTOCOL_VERSION)
        self.assertEqual(payload["session_id"], "session-test")
        self.assertEqual(payload["completed"], 2)
        self.assertEqual(payload["total"], 4)
        self.assertGreater(payload["eta_sec"], 0)

    def test_session_write_is_archived_and_identified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            session_dir = root / "sessions" / "session-test"
            session = {
                "session_id": "session-test",
                "session_output_dir": str(session_dir),
                "incidents": [],
            }
            latest = write_latest_session(session, root)
            archive = session_dir / "session.json"
            self.assertTrue(latest.exists())
            self.assertTrue(archive.exists())
            written = json.loads(latest.read_text(encoding="utf-8"))
            self.assertEqual(written["session_id"], "session-test")
            self.assertEqual(Path(written["session_archive_path"]), archive.resolve())
            self.assertFalse(latest.with_suffix(".json.tmp").exists())

    def test_camera_mask_can_be_user_corrected(self) -> None:
        default, source = polygons_for_camera("left_repeater")
        self.assertEqual(source, "camera_template")
        self.assertTrue(default)
        custom = {"cameras": {"left_repeater": {"polygons": [[[0.1, 0.2], [0.2, 0.2], [0.2, 0.3]]]}}}
        polygons, source = polygons_for_camera("left_repeater", custom)
        self.assertEqual(source, "user_calibration")
        self.assertEqual(polygons, custom["cameras"]["left_repeater"]["polygons"])

    def test_source_hash_inventory_detects_byte_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            video = Path(temporary) / "source.mp4"
            video.write_bytes(b"original source bytes")
            before = source_video_hashes(temporary)
            video.write_bytes(b"changed source bytes")
            after = source_video_hashes(temporary)
            self.assertNotEqual(before, after)


if __name__ == "__main__":
    unittest.main()
