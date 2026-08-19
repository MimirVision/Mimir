"""Transactional file-action regression checks using real temporary files."""

from __future__ import annotations

import subprocess
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

    def test_move_to_trash_removes_the_whole_source_event_folder(self) -> None:
        # The real-world request this covers: after Move to Mimir Trash, no
        # trace of the incident's folder should remain on the USB stick --
        # not just the tracked .mp4s, but Tesla's own untracked thumb.png
        # sitting alongside them too.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_dir = root / "usb" / "TeslaCam" / "SentryClips" / "2026-07-15_14-30-00"
            source_dir.mkdir(parents=True)
            paths = [source_dir / "front.mp4", source_dir / "rear.mp4"]
            for index, path in enumerate(paths):
                path.write_bytes((f"clip-{index}").encode())
            (source_dir / "thumb.png").write_bytes(b"not a real thumbnail")

            incident = incident_for(paths)
            incident["event_folder"] = str(source_dir)
            incident["source_category"] = "SentryClips"
            session = {"incidents": [incident]}

            actions.LIBRARY_ROOT = root / "library"
            actions.TRASH_ROOT = actions.LIBRARY_ROOT / "_Mimir Trash"
            report = actions.perform_action(session, [incident], "move_to_trash", False, root / "journal.json")

            self.assertTrue(report["ok"])
            self.assertFalse(source_dir.exists(), "the whole source event folder should be gone")
            self.assertEqual(len(report["source_folders_removed"]), 1)
            self.assertTrue(report["source_folders_removed"][0]["removed"])
            # The clips themselves are not lost -- just relocated to Trash.
            self.assertTrue(any(actions.TRASH_ROOT.rglob("front.mp4")))

    def test_recent_clips_folder_is_never_removed(self) -> None:
        # RecentClips is one flat, continuously-recorded folder shared by
        # unrelated moments -- unlike SentryClips/SavedClips, deleting it
        # would destroy footage that has nothing to do with this incident.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_dir = root / "usb" / "TeslaCam" / "RecentClips"
            source_dir.mkdir(parents=True)
            paths = [source_dir / "front.mp4"]
            paths[0].write_bytes(b"clip-0")

            incident = incident_for(paths)
            incident["event_folder"] = str(source_dir)
            incident["source_category"] = "RecentClips"
            session = {"incidents": [incident]}

            actions.LIBRARY_ROOT = root / "library"
            actions.TRASH_ROOT = actions.LIBRARY_ROOT / "_Mimir Trash"
            report = actions.perform_action(session, [incident], "move_to_trash", False, root / "journal.json")

            self.assertTrue(report["ok"])
            self.assertTrue(source_dir.exists(), "a shared RecentClips folder must survive")
            self.assertFalse(report["source_folders_removed"][0]["removed"])

    def test_a_shared_folder_survives_even_when_spelled_differently(self) -> None:
        """The sibling check must survive two spellings of one folder.

        The incident's own folder is resolved before comparison; the other
        incident's is whatever the session recorded. Where those differ -- a
        junction, a mapped drive, an 8.3 short name like RUNNER~1 -- the sibling
        was skipped and the folder removed while that incident still had clips
        in it. CI hit this for eleven days: its temp directory and its checkout
        are on different volumes and the paths did not match textually.
        """

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            real = root / "real" / "SentryClips" / "2026-07-15_14-30-00"
            real.mkdir(parents=True)
            junction = root / "via_junction"
            created = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(junction), str(root / "real")],
                capture_output=True,
            )
            if created.returncode != 0:
                self.skipTest("could not create a junction on this filesystem")

            through_junction = junction / "SentryClips" / "2026-07-15_14-30-00"
            self.assertNotEqual(
                actions.path_key(str(through_junction)),
                actions.path_key(str(real.resolve())),
                "the two spellings must actually differ, or this proves nothing",
            )
            self.assertTrue(actions._same_folder(through_junction, real.resolve()))

    def test_shared_source_folder_is_not_removed(self) -> None:
        # Defense in depth: even a folder-per-event category is left alone if
        # another incident's clips are recorded as coming from the same
        # folder, since blindly deleting could destroy that incident's data.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_dir = root / "usb" / "TeslaCam" / "SentryClips" / "2026-07-15_14-30-00"
            source_dir.mkdir(parents=True)
            paths = [source_dir / "front.mp4"]
            paths[0].write_bytes(b"clip-0")

            incident = incident_for(paths)
            incident["id"] = "incident_a"
            incident["event_group_id"] = "group_a"
            incident["event_folder"] = str(source_dir)
            incident["source_category"] = "SentryClips"
            other_incident = {
                "id": "incident_b",
                "event_group_id": "group_b",
                "event_folder": str(source_dir),
            }
            session = {"incidents": [incident, other_incident]}

            actions.LIBRARY_ROOT = root / "library"
            actions.TRASH_ROOT = actions.LIBRARY_ROOT / "_Mimir Trash"
            report = actions.perform_action(session, [incident], "move_to_trash", False, root / "journal.json")

            self.assertTrue(report["ok"])
            self.assertTrue(source_dir.exists(), "a folder shared with another incident must survive")
            self.assertIn("share this source folder", report["source_folders_removed"][0]["reason"])

    def test_dry_run_never_removes_the_source_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_dir = root / "usb" / "TeslaCam" / "SentryClips" / "2026-07-15_14-30-00"
            source_dir.mkdir(parents=True)
            paths = [source_dir / "front.mp4"]
            paths[0].write_bytes(b"clip-0")

            incident = incident_for(paths)
            incident["event_folder"] = str(source_dir)
            incident["source_category"] = "SentryClips"
            session = {"incidents": [incident]}

            actions.LIBRARY_ROOT = root / "library"
            actions.TRASH_ROOT = actions.LIBRARY_ROOT / "_Mimir Trash"
            result = actions.remove_incident_source_folder(session, incident, True)

            self.assertTrue(source_dir.exists())
            self.assertFalse(result["removed"])
            self.assertTrue(result.get("would_remove"))

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
