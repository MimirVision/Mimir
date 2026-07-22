"""Transactional file-action regression checks using real temporary files."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import mimir_core_v2_actions as actions


def incident_for(paths: list[Path]) -> dict:
    return {
        "id": "incident_test",
        "event_group_id": "group_test",
        "final_severity": "IMPORTANT",
        "primary_camera": "front",
        "video_path": str(paths[0]),
        "camera_count": len(paths),
        "camera_clips": [
            {"camera": "front" if index == 0 else "rear", "path": str(path), "filename": path.name}
            for index, path in enumerate(paths)
        ],
        "storage_state": "source",
    }


class StorageActionTests(unittest.TestCase):
    def test_group_move_rolls_back_if_any_file_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_dir = root / "source"
            source_dir.mkdir()
            paths = [source_dir / "front.mp4", source_dir / "rear.mp4"]
            for index, path in enumerate(paths):
                path.write_bytes((f"clip-{index}").encode())
            incident = incident_for(paths)
            actions.LIBRARY_ROOT = root / "library"
            actions.TRASH_ROOT = actions.LIBRARY_ROOT / "_Mimir Trash"
            real_move = actions.safe_move_file

            def fail_second(source: Path, destination: Path, dry_run: bool):
                if source == paths[1]:
                    return {"source": str(source), "destination": str(destination), "ok": False, "skipped": False, "dry_run": dry_run, "error": "simulated disk failure"}
                return real_move(source, destination, dry_run)

            journal = root / "journal.json"
            with patch.object(actions, "safe_move_file", side_effect=fail_second):
                report = actions.perform_action({}, [incident], "move_to_library", False, journal)
            self.assertFalse(report["ok"])
            self.assertEqual(report["transaction_state"], "rolled_back")
            self.assertTrue(all(path.exists() for path in paths))
            self.assertEqual(incident["storage_state"], "source")
            self.assertTrue(journal.exists())

    def test_restore_returns_file_to_original_location(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / "source" / "front.mp4"
            trash = root / "library" / "_Mimir Trash" / "front.mp4"
            trash.parent.mkdir(parents=True)
            trash.write_bytes(b"clip")
            incident = incident_for([trash])
            incident["original_source_video"] = str(original)
            incident["camera_clips"][0]["original_path"] = str(original)
            incident["storage_state"] = "trash"
            actions.LIBRARY_ROOT = root / "library"
            actions.TRASH_ROOT = actions.LIBRARY_ROOT / "_Mimir Trash"
            report = actions.perform_action({}, [incident], "restore_from_trash", False, root / "journal.json")
            self.assertTrue(report["ok"])
            self.assertTrue(original.exists())
            self.assertFalse(trash.exists())
            self.assertEqual(incident["storage_state"], "source")


if __name__ == "__main__":
    unittest.main()
