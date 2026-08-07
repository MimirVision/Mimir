"""Deleting is the one action Mimir cannot take back, so the guards get tests.

Everything here runs with --no-recycle-bin (a real unlink) against temporary
directories. The Recycle Bin path is covered in test_recycle_bin.py against the
actual shell API; mixing the two would put a test fixture in the developer's
Recycle Bin on every run.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mimir_core_v2_actions as actions


def build_event_folder(root: Path, name: str = "2026-05-14_14-56-27") -> Path:
    """A Tesla event folder: the clips, plus the metadata Tesla writes beside them."""

    folder = root / "TeslaCam" / "SentryClips" / name
    folder.mkdir(parents=True)
    for camera in ("front", "back", "left_repeater", "right_repeater"):
        (folder / f"2026-05-14_14-56-20-{camera}.mp4").write_bytes(b"clip" * 256)
    (folder / "event.json").write_text('{"timestamp": "2026-05-14T14:56:20"}', encoding="utf-8")
    (folder / "thumb.png").write_bytes(b"png")
    return folder


def build_incident(folder: Path, thumbnails: Path) -> dict:
    clips = sorted(folder.glob("*.mp4"))
    hero = thumbnails / "incident_0199_hero.jpg"
    sheet = thumbnails / "incident_0199_contact_sheet.jpg"
    hero.write_bytes(b"jpg")
    sheet.write_bytes(b"jpg")
    return {
        "id": "incident_0199",
        "event_group_id": "group_0199",
        # Required, and rightly so: only SentryClips and SavedClips put one
        # event in its own folder. RecentClips is a flat directory shared by
        # every clip on the card, so removing "the folder" there would take
        # footage belonging to other incidents with it.
        "source_category": "SentryClips",
        "event_folder": str(folder),
        "video_path": str(clips[0]),
        "final_severity": "REVIEW",
        "camera_clips": [
            {"camera": path.stem.split("-")[-1], "path": str(path), "filename": path.name}
            for path in clips
        ],
        "thumbnail": str(hero),
        "hero_thumbnail": str(hero),
        "contact_sheet": str(sheet),
    }


class DeletionScopeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

        self.library = root / "library"
        (self.library / "_Mimir Trash").mkdir(parents=True)
        self.thumbnails = root / "session" / "thumbnails"
        self.thumbnails.mkdir(parents=True)

        self._previous_library = actions.LIBRARY_ROOT
        self._previous_trash = actions.TRASH_ROOT
        actions.LIBRARY_ROOT = self.library
        actions.TRASH_ROOT = self.library / "_Mimir Trash"
        self.addCleanup(self._restore_roots)

        self.folder = build_event_folder(root)
        self.incident = build_incident(self.folder, self.thumbnails)
        self.session = {"incidents": [self.incident]}

    def _restore_roots(self) -> None:
        actions.LIBRARY_ROOT = self._previous_library
        actions.TRASH_ROOT = self._previous_trash

    def test_deleting_reaches_clips_event_folder_and_generated_thumbnails(self) -> None:
        # The gap this closes: move_to_trash left Mimir's own thumbnails in the
        # session directory, so a "deleted" incident still had a picture of
        # itself on disk.
        report = actions.delete_incidents(self.session, [self.incident], use_recycle_bin=False, dry_run=False)

        self.assertTrue(report["ok"], report["failures"])
        self.assertFalse(self.folder.exists(), "The Tesla event folder is still on the drive.")
        self.assertFalse(
            (self.thumbnails / "incident_0199_hero.jpg").exists(),
            "The generated hero thumbnail survived the delete.",
        )
        self.assertFalse((self.thumbnails / "incident_0199_contact_sheet.jpg").exists())

    def test_the_incident_is_marked_deleted_so_the_review_screen_drops_it(self) -> None:
        actions.delete_incidents(self.session, [self.incident], use_recycle_bin=False, dry_run=False)

        # IncidentLibraryView filters on user_deleted.
        self.assertTrue(self.incident["user_deleted"])
        self.assertEqual(self.incident["storage_state"], "deleted")
        self.assertFalse(self.incident["video_exists"])

    def test_a_dry_run_reports_the_real_size_and_deletes_nothing(self) -> None:
        report = actions.delete_incidents(self.session, [self.incident], use_recycle_bin=False, dry_run=True)

        self.assertTrue(self.folder.exists())
        self.assertTrue((self.thumbnails / "incident_0199_hero.jpg").exists())
        self.assertGreater(
            report["bytes_deleted"],
            0,
            "A confirmation dialog needs a real byte count, otherwise it can only ask 'are you sure?'.",
        )

    def test_a_folder_shared_with_another_incident_is_left_alone(self) -> None:
        # Two incidents from one event group must not have the folder pulled
        # out from under the one that was not selected.
        other = dict(self.incident)
        other["id"] = "incident_0200"
        other["event_group_id"] = "group_0200"
        session = {"incidents": [self.incident, other]}

        targets = actions.deletion_targets(session, self.incident)

        self.assertEqual(targets["source_folder"], "")
        self.assertIn("another incident", targets["source_folder_reason"])

    def test_a_sibling_that_is_already_gone_no_longer_protects_the_folder(self) -> None:
        """Otherwise a shared folder can never be cleared off the card.

        A Tesla event folder holds several minute-long clips and Mimir groups
        on (folder, timestamp), so one folder routinely becomes two or three
        incidents. If any sibling counts as a blocker regardless of its state,
        the user can empty every incident out of a folder and still be left
        with the folder, its event.json and its thumbnail on the USB drive.
        """

        sibling = dict(self.incident)
        sibling["id"] = "incident_0200"
        sibling["event_group_id"] = "group_0200"
        session = {"incidents": [self.incident, sibling]}

        # While the sibling still holds footage there, the folder is protected.
        self.assertEqual(actions.deletion_targets(session, self.incident)["source_folder"], "")

        # Once its clips have been trashed, it has nothing left to protect.
        sibling["user_deleted"] = True
        sibling["storage_state"] = "trash"

        targets = actions.deletion_targets(session, self.incident)
        self.assertEqual(targets["source_folder"], str(self.folder))

        actions.delete_incidents(session, [self.incident], use_recycle_bin=False, dry_run=False)
        self.assertFalse(self.folder.exists(), "The event folder stayed on the drive.")

    def test_a_flat_recentclips_layout_never_has_its_folder_removed(self) -> None:
        # RecentClips is one directory holding every clip on the card. Deleting
        # "the event folder" there would delete the whole recording buffer.
        self.incident["source_category"] = "RecentClips"

        targets = actions.deletion_targets(self.session, self.incident)

        self.assertEqual(targets["source_folder"], "")
        self.assertIn("folder-per-event", targets["source_folder_reason"])

        actions.delete_incidents(self.session, [self.incident], use_recycle_bin=False, dry_run=False)
        self.assertTrue(
            self.folder.exists(),
            "The shared RecentClips directory was removed, taking unrelated footage with it.",
        )


class EmptyTrashTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self._previous_library = actions.LIBRARY_ROOT
        self._previous_trash = actions.TRASH_ROOT
        self.addCleanup(self._restore_roots)

    def _restore_roots(self) -> None:
        actions.LIBRARY_ROOT = self._previous_library
        actions.TRASH_ROOT = self._previous_trash

    def test_emptying_removes_everything_and_reports_the_space(self) -> None:
        library = self.root / "library"
        trash = library / "_Mimir Trash"
        trash.mkdir(parents=True)
        (trash / "front.mp4").write_bytes(b"x" * 5000)
        nested = trash / "2026-05-14_14-56-27"
        nested.mkdir()
        (nested / "back.mp4").write_bytes(b"x" * 5000)

        actions.LIBRARY_ROOT = library
        actions.TRASH_ROOT = trash

        report = actions.empty_trash(use_recycle_bin=False, dry_run=False)

        self.assertTrue(report["ok"], report["failures"])
        self.assertEqual(report["files_found"], 2)
        self.assertEqual(report["bytes_deleted"], 10000)
        self.assertTrue(trash.is_dir(), "The trash folder itself should survive being emptied.")
        self.assertEqual(list(trash.iterdir()), [])

    def test_it_refuses_when_the_trash_root_is_not_inside_the_library(self) -> None:
        # --library-root is user-supplied. A mistake there must not turn
        # "empty the trash" into "empty that folder".
        library = self.root / "library"
        library.mkdir()
        elsewhere = self.root / "important-documents"
        elsewhere.mkdir()
        (elsewhere / "taxes.pdf").write_bytes(b"keep me")

        actions.LIBRARY_ROOT = library
        actions.TRASH_ROOT = elsewhere

        report = actions.empty_trash(use_recycle_bin=False, dry_run=False)

        self.assertFalse(report["ok"])
        self.assertIn("not inside the Mimir Library", report["failures"][0]["reason"])
        self.assertTrue((elsewhere / "taxes.pdf").exists(), "It deleted files outside the library.")

    def test_it_refuses_to_empty_the_library_root_itself(self) -> None:
        library = self.root / "library"
        library.mkdir()
        (library / "Important").mkdir()
        actions.LIBRARY_ROOT = library
        actions.TRASH_ROOT = library

        report = actions.empty_trash(use_recycle_bin=False, dry_run=False)

        self.assertFalse(report["ok"])
        self.assertTrue((library / "Important").exists())

    def test_an_empty_trash_is_a_success_not_an_error(self) -> None:
        library = self.root / "library"
        trash = library / "_Mimir Trash"
        trash.mkdir(parents=True)
        actions.LIBRARY_ROOT = library
        actions.TRASH_ROOT = trash

        report = actions.empty_trash(use_recycle_bin=False, dry_run=False)

        self.assertTrue(report["ok"])
        self.assertEqual(report["files_found"], 0)


class RecycleBinHonestyTest(unittest.TestCase):
    def test_a_recycle_bin_failure_is_never_downgraded_to_a_real_delete(self) -> None:
        """Someone who chose the recoverable option has to actually get it."""

        with tempfile.TemporaryDirectory() as raw:
            target = Path(raw) / "clip.mp4"
            target.write_bytes(b"precious")

            original = actions.recycle_bin.available
            actions.recycle_bin.available = lambda: False
            try:
                results = actions._remove_paths([target], use_recycle_bin=True)
            finally:
                actions.recycle_bin.available = original

            self.assertFalse(results[0]["ok"])
            self.assertTrue(
                target.exists(),
                "The file was hard-deleted after the Recycle Bin was unavailable. That silently "
                "converts a recoverable delete into an unrecoverable one.",
            )


if __name__ == "__main__":
    unittest.main()
