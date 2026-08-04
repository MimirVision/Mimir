"""Reliability harness contract tests."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from mimir_core_v2_reliability import copy_fixture, scanner_command, weighted_scenarios


class ReliabilityHarnessTests(unittest.TestCase):
    def test_weighted_scenarios_exercise_each_mode_before_repeating(self) -> None:
        config = {
            "scenarios": [
                {"name": "normal", "weight": 3},
                {"name": "corrupt", "weight": 1},
                {"name": "cancel", "weight": 1},
            ]
        }
        names = [item["name"] for item in weighted_scenarios(config)]
        self.assertEqual(names[:3], ["normal", "corrupt", "cancel"])
        self.assertEqual(names.count("normal"), 3)

    def test_packaged_scanner_command_does_not_require_python(self) -> None:
        command = scanner_command(
            Path("mimir-core-v2-scan.exe"),
            Path("fixture"),
            Path("output"),
            ["--disable-yolo"],
        )
        self.assertEqual(command[0], "mimir-core-v2-scan.exe")
        self.assertNotEqual(command[0], sys.executable)
        self.assertIn("--disable-yolo", command)

    def test_corruption_is_limited_to_temporary_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            destination = root / "copy"
            source.mkdir()
            original = b"real fixture bytes" * 100
            source_video = source / "clip.mp4"
            source_video.write_bytes(original)
            copied = copy_fixture(source, destination, "corrupt")
            self.assertEqual(source_video.read_bytes(), original)
            self.assertEqual(len(copied), 1)
            self.assertNotEqual(copied[0].read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
