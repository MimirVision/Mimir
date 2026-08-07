"""Importing footage deletes from the user's only copy, so the guards get tests.

The property that matters most is negative: nothing is removed from the source
unless a verified copy exists. Several tests here corrupt or block the copy on
purpose and then assert the original survived.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mimir_core_v2 import footage_import
from mimir_core_v2.validators import source_category_for_path


def make_event(root: Path, category: str, name: str, clips: int = 4) -> Path:
    folder = root / "TeslaCam" / category / name
    folder.mkdir(parents=True)
    for index in range(clips):
        (folder / f"2026-05-14_14-56-2{index}-front.mp4").write_bytes(bytes([index]) * 4096)
    # Tesla writes these beside the clips. If they are left behind the folder
    # never disappears from the card, which defeats the purpose.
    (folder / "event.json").write_text('{"reason":"sentry_aware_object_detection"}', encoding="utf-8")
    (folder / "thumb.png").write_bytes(b"png-bytes")
    return folder


class PlanTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.destination = self.root / "library" / "Footage"

    def test_the_category_folder_survives_the_move(self) -> None:
        """Otherwise the copy scans as 'generic_folder' and storage actions break.

        source_category_for_path looks for SentryClips/SavedClips/RecentClips
        anywhere in the path. Lose it and the scan of the imported copy no
        longer knows the layout is folder-per-event, which is exactly what
        source_folder_removal_eligibility requires before it will remove
        anything.
        """

        make_event(self.root, "SentryClips", "2026-05-14_14-56-27")
        plan = footage_import.plan_import(self.root / "TeslaCam", self.destination)

        self.assertEqual(plan.event_count, 1)
        destination = plan.items[0].destination_folder
        self.assertEqual(destination, self.destination / "SentryClips" / "2026-05-14_14-56-27")
        self.assertEqual(source_category_for_path(destination / "x.mp4"), "SentryClips")

    def test_the_category_survives_when_the_user_picks_the_category_folder_itself(self) -> None:
        # Selecting D:\TeslaCam\SentryClips rather than D:\TeslaCam is the
        # normal thing to do, and it must not flatten the category away.
        make_event(self.root, "SentryClips", "2026-05-14_14-56-27")
        plan = footage_import.plan_import(self.root / "TeslaCam" / "SentryClips", self.destination)

        destination = plan.items[0].destination_folder
        self.assertEqual(source_category_for_path(destination / "x.mp4"), "SentryClips")

    def test_metadata_files_are_included_not_just_videos(self) -> None:
        make_event(self.root, "SentryClips", "2026-05-14_14-56-27")
        plan = footage_import.plan_import(self.root / "TeslaCam", self.destination)

        names = {path.name for path in plan.items[0].files}
        self.assertIn("event.json", names)
        self.assertIn("thumb.png", names)

    def test_importing_into_the_source_is_refused(self) -> None:
        make_event(self.root, "SentryClips", "2026-05-14_14-56-27")
        source = self.root / "TeslaCam"

        plan = footage_import.plan_import(source, source / "imported")

        self.assertEqual(plan.items, [])
        self.assertTrue(any("inside the folder" in warning for warning in plan.warnings))


class ImportTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.source = self.root / "TeslaCam"
        self.destination = self.root / "library" / "Footage"
        self.folder = make_event(self.root, "SentryClips", "2026-05-14_14-56-27")

    def test_everything_arrives_and_the_source_folder_goes(self) -> None:
        report = footage_import.import_footage(
            self.source, self.destination, remove_source=True, dry_run=False
        )

        self.assertTrue(report["ok"], report["failures"])
        self.assertEqual(report["files_copied"], 6)
        self.assertEqual(report["source_folders_removed"], 1)
        self.assertFalse(self.folder.exists(), "The event folder is still on the source drive.")

        arrived = self.destination / "SentryClips" / "2026-05-14_14-56-27"
        self.assertTrue((arrived / "event.json").exists())
        self.assertTrue((arrived / "thumb.png").exists())
        self.assertEqual(len(list(arrived.glob("*.mp4"))), 4)

    def test_contents_are_identical_after_the_move(self) -> None:
        original = (self.folder / "2026-05-14_14-56-20-front.mp4").read_bytes()

        footage_import.import_footage(self.source, self.destination, remove_source=True, dry_run=False)

        arrived = self.destination / "SentryClips" / "2026-05-14_14-56-27" / "2026-05-14_14-56-20-front.mp4"
        self.assertEqual(arrived.read_bytes(), original)

    def test_a_dry_run_reports_the_work_and_moves_nothing(self) -> None:
        report = footage_import.import_footage(
            self.source, self.destination, remove_source=True, dry_run=True
        )

        self.assertEqual(report["files_found"], 6)
        self.assertGreater(report["bytes_found"], 0)
        self.assertEqual(report["files_copied"], 0)
        self.assertTrue(self.folder.exists())
        self.assertFalse(self.destination.exists())

    def test_copy_only_leaves_the_source_untouched(self) -> None:
        report = footage_import.import_footage(
            self.source, self.destination, remove_source=False, dry_run=False
        )

        self.assertTrue(report["ok"])
        self.assertEqual(report["source_files_removed"], 0)
        self.assertTrue(self.folder.exists())
        self.assertEqual(len(list(self.folder.glob("*.mp4"))), 4)

    def test_a_failed_verification_keeps_the_original(self) -> None:
        """The property the whole design exists to protect.

        The drive that motivated this returns I/O errors. If a copy ever comes
        back wrong, the original is the only remaining copy of that footage and
        it must survive.
        """

        real_hash = footage_import._hash_file

        # On a fresh copy _hash_file is called exactly once per file -- the
        # read-back of the destination, since the source digest comes from
        # _copy_and_hash as the bytes go past. Returning nonsense for every
        # call is therefore "every copy arrived corrupt".
        footage_import._hash_file = lambda path: "0" * 64
        try:
            report = footage_import.import_footage(
                self.source, self.destination, remove_source=True, dry_run=False
            )
        finally:
            footage_import._hash_file = real_hash

        self.assertFalse(report["ok"])
        self.assertEqual(report["source_files_removed"], 0)
        self.assertTrue(self.folder.exists(), "The source folder was removed despite a bad copy.")
        self.assertEqual(
            len(list(self.folder.glob("*.mp4"))),
            4,
            "Original clips were deleted even though verification failed.",
        )

    def test_a_partial_failure_removes_only_the_files_that_verified(self) -> None:
        """A bad copy must not take its innocent neighbours down with it.

        The first version of this test assumed otherwise and failed, which was
        the test being wrong rather than the code: removal is decided per file,
        so the ones that verified go and the ones that did not stay. That is
        the behaviour worth keeping, so it is pinned here explicitly.
        """

        real_hash = footage_import._hash_file
        calls = {"n": 0}

        def corrupt_every_other(path: Path) -> str:
            calls["n"] += 1
            return "0" * 64 if calls["n"] % 2 == 0 else real_hash(path)

        footage_import._hash_file = corrupt_every_other
        try:
            report = footage_import.import_footage(
                self.source, self.destination, remove_source=True, dry_run=False
            )
        finally:
            footage_import._hash_file = real_hash

        self.assertFalse(report["ok"])
        self.assertEqual(len(report["failures"]), 3)
        self.assertEqual(report["source_files_removed"], 3)
        self.assertTrue(
            self.folder.exists(),
            "The folder went even though three originals were still in it.",
        )
        self.assertEqual(
            len(list(self.folder.iterdir())),
            3,
            "Exactly the three unverified originals should remain.",
        )

    def test_rerunning_finishes_an_interrupted_import(self) -> None:
        # First pass copies without clearing the source, standing in for a run
        # that died after copying.
        footage_import.import_footage(self.source, self.destination, remove_source=False, dry_run=False)

        report = footage_import.import_footage(
            self.source, self.destination, remove_source=True, dry_run=False
        )

        self.assertTrue(report["ok"], report["failures"])
        self.assertEqual(report["files_skipped"], 6, "Identical files were copied again.")
        self.assertEqual(report["files_copied"], 0)
        self.assertFalse(self.folder.exists(), "The second run did not clear the source.")

    def test_a_partial_file_from_a_dead_run_is_replaced_not_trusted(self) -> None:
        arrived = self.destination / "SentryClips" / "2026-05-14_14-56-27"
        arrived.mkdir(parents=True)
        # Same name, wrong contents and wrong length: what a truncated copy
        # looks like.
        (arrived / "2026-05-14_14-56-20-front.mp4").write_bytes(b"truncated")

        report = footage_import.import_footage(
            self.source, self.destination, remove_source=True, dry_run=False
        )

        self.assertTrue(report["ok"], report["failures"])
        self.assertEqual(
            (arrived / "2026-05-14_14-56-20-front.mp4").read_bytes(),
            bytes([0]) * 4096,
        )

    def test_no_partial_files_are_left_behind(self) -> None:
        footage_import.import_footage(self.source, self.destination, remove_source=True, dry_run=False)

        leftovers = list(self.destination.rglob("*.mimir-partial"))
        self.assertEqual(leftovers, [], f"Staging files survived: {leftovers}")

    def test_several_events_and_categories_move_together(self) -> None:
        make_event(self.root, "SentryClips", "2026-05-14_15-10-00")
        make_event(self.root, "SavedClips", "2026-05-14_16-00-00")

        report = footage_import.import_footage(
            self.source, self.destination, remove_source=True, dry_run=False
        )

        self.assertTrue(report["ok"], report["failures"])
        self.assertEqual(report["events_found"], 3)
        self.assertEqual(report["source_folders_removed"], 3)
        self.assertTrue((self.destination / "SentryClips" / "2026-05-14_15-10-00").is_dir())
        self.assertTrue((self.destination / "SavedClips" / "2026-05-14_16-00-00").is_dir())

    def test_folders_holding_only_tesla_metadata_are_swept_up(self) -> None:
        """Otherwise the stick is not actually empty afterwards.

        Real Tesla drives carry event folders whose clips are already gone,
        leaving just event.json and thumb.png. plan_import ignores them,
        correctly, because they have no video to copy -- so without a sweep
        they are what remains on a drive the user was told had been cleared.
        Found on the real drive: its two smallest event folders had no clips.
        """

        orphan = self.source / "SentryClips" / "2026-04-01_08-00-00"
        orphan.mkdir(parents=True)
        (orphan / "event.json").write_text("{}", encoding="utf-8")
        (orphan / "thumb.png").write_bytes(b"png")

        report = footage_import.import_footage(
            self.source, self.destination, remove_source=True, dry_run=False
        )

        self.assertTrue(report["ok"], report["failures"])
        self.assertFalse(orphan.exists(), "A metadata-only folder was left on the drive.")

    def test_the_sweep_never_touches_files_the_user_put_there(self) -> None:
        keep = self.source / "SentryClips" / "my-own-stuff"
        keep.mkdir(parents=True)
        (keep / "holiday-photos.zip").write_bytes(b"not tesla's")

        footage_import.import_footage(
            self.source, self.destination, remove_source=True, dry_run=False
        )

        self.assertTrue(
            (keep / "holiday-photos.zip").exists(),
            "The sweep deleted a file that has nothing to do with Mimir.",
        )

    def test_nothing_is_swept_when_a_copy_failed(self) -> None:
        # A drive that still holds unimported footage must not be made to look
        # tidy, or the user assumes everything arrived.
        orphan = self.source / "SentryClips" / "2026-04-01_08-00-00"
        orphan.mkdir(parents=True)
        (orphan / "event.json").write_text("{}", encoding="utf-8")

        real_hash = footage_import._hash_file
        footage_import._hash_file = lambda path: "0" * 64
        try:
            report = footage_import.import_footage(
                self.source, self.destination, remove_source=True, dry_run=False
            )
        finally:
            footage_import._hash_file = real_hash

        self.assertFalse(report["ok"])
        self.assertTrue(orphan.exists())

    def test_progress_is_reported_for_every_file(self) -> None:
        events: list[dict] = []

        footage_import.import_footage(
            self.source,
            self.destination,
            remove_source=False,
            dry_run=False,
            on_progress=events.append,
        )

        copying = [event for event in events if event["stage"] == "copying"]
        self.assertEqual(len(copying), 6)
        self.assertEqual(copying[-1]["percent"], 100.0)
        self.assertEqual(events[-1]["stage"], "complete")


if __name__ == "__main__":
    unittest.main()
