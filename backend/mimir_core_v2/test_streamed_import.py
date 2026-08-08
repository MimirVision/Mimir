"""Per-group import: copy, verify, scan, then clear.

These cover the relocation unit rather than a whole scan, because a real scan
needs the detector and a minute of decoding. The property that matters is the
same at either level: nothing leaves the source drive unless a verified copy
of it exists somewhere else.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mimir_core_v2 import footage_import
from mimir_core_v2.footage_import import relocate_event_group, remove_relocated_sources
from mimir_core_v2.validators import source_category_for_path


def make_group(root: Path, name: str = "2026-05-14_14-56-27", clips: int = 4) -> tuple[dict, Path]:
    folder = root / "TeslaCam" / "SentryClips" / name
    folder.mkdir(parents=True)
    cameras = ["front", "back", "left_repeater", "right_repeater"][:clips]
    for index, camera in enumerate(cameras):
        (folder / f"2026-05-14_14-56-20-{camera}.mp4").write_bytes(bytes([index + 1]) * 3000)
    (folder / "event.json").write_text('{"reason":"sentry"}', encoding="utf-8")
    (folder / "thumb.png").write_bytes(b"png")

    group = {
        "event_group_id": "group_0001",
        "event_folder": str(folder),
        "source_category": "SentryClips",
        "clips": [
            {"camera": camera, "path": str(folder / f"2026-05-14_14-56-20-{camera}.mp4")}
            for camera in cameras
        ],
    }
    return group, folder


class RelocateGroupTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.library = self.root / "library" / "Footage"
        self.group, self.folder = make_group(self.root)

    def test_the_group_is_copied_and_rewritten_to_point_locally(self) -> None:
        result = relocate_event_group(self.group, self.library)

        self.assertTrue(result.ok, result.failures)
        self.assertEqual(result.copied, 6)
        for clip in self.group["clips"]:
            self.assertTrue(Path(clip["path"]).is_file())
            self.assertTrue(str(self.library) in clip["path"], "clip still points at the source drive")
            self.assertIn("original_path", clip)

    def test_the_category_survives_so_folder_removal_still_applies_later(self) -> None:
        # Lose SentryClips from the path and a scan of the copy reports
        # generic_folder, which source_folder_removal_eligibility refuses to
        # act on -- the delete feature would silently stop working.
        result = relocate_event_group(self.group, self.library)

        self.assertEqual(
            source_category_for_path(result.destination_folder / "x.mp4"), "SentryClips"
        )

    def test_tesla_metadata_travels_with_the_clips(self) -> None:
        result = relocate_event_group(self.group, self.library)

        self.assertTrue((result.destination_folder / "event.json").is_file())
        self.assertTrue((result.destination_folder / "thumb.png").is_file())

    def test_nothing_is_deleted_by_relocating(self) -> None:
        """Removal is a separate call made after the scan, on purpose.

        Deleting at copy time would be safe for the data -- the verified copy
        exists -- but a crash mid-scan would leave the drive half emptied for
        no benefit.
        """

        relocate_event_group(self.group, self.library)

        self.assertTrue(self.folder.is_dir())
        self.assertEqual(len(list(self.folder.glob("*.mp4"))), 4)

    def test_clearing_afterwards_removes_the_files_and_the_folder(self) -> None:
        result = relocate_event_group(self.group, self.library)
        removal = remove_relocated_sources(result)

        self.assertEqual(removal["removed"], 6)
        self.assertTrue(removal["folder_removed"])
        self.assertFalse(self.folder.exists())

    def test_a_bad_copy_leaves_the_group_pointing_at_the_original(self) -> None:
        """The property the whole design exists to protect."""

        real_hash = footage_import._hash_file
        footage_import._hash_file = lambda path: "0" * 64
        try:
            result = relocate_event_group(self.group, self.library)
        finally:
            footage_import._hash_file = real_hash

        self.assertFalse(result.ok)
        self.assertEqual(result.removable_sources, [])
        for clip in self.group["clips"]:
            self.assertTrue(
                str(self.folder) in clip["path"],
                "the group was repointed at a copy that failed verification",
            )
        self.assertTrue(self.folder.is_dir())
        self.assertEqual(len(list(self.folder.glob("*.mp4"))), 4)

    def test_clearing_refuses_a_group_that_did_not_fully_copy(self) -> None:
        real_hash = footage_import._hash_file
        footage_import._hash_file = lambda path: "0" * 64
        try:
            result = relocate_event_group(self.group, self.library)
        finally:
            footage_import._hash_file = real_hash

        removal = remove_relocated_sources(result)

        self.assertEqual(removal["removed"], 0)
        self.assertTrue(self.folder.is_dir())

    def test_a_file_the_user_added_keeps_its_folder_alive(self) -> None:
        result = relocate_event_group(self.group, self.library)
        (self.folder / "my-notes.txt").write_text("mine", encoding="utf-8")

        removal = remove_relocated_sources(result)

        self.assertFalse(removal["folder_removed"])
        self.assertTrue((self.folder / "my-notes.txt").is_file())

    def test_rerunning_after_an_interruption_skips_what_already_arrived(self) -> None:
        relocate_event_group(self.group, self.library)
        # A fresh group object, as a re-run would build it from discovery.
        group2 = {
            "event_group_id": "group_0001",
            "event_folder": str(self.folder),
            "source_category": "SentryClips",
            "clips": [
                {"camera": "front", "path": str(self.folder / "2026-05-14_14-56-20-front.mp4")}
            ],
        }

        result = relocate_event_group(group2, self.library)

        self.assertTrue(result.ok, result.failures)
        self.assertEqual(result.copied, 0, "identical files were copied a second time")
        self.assertEqual(result.skipped, 6)


class ReconcilePartialsTest(unittest.TestCase):
    """Staging files left behind when a copy is killed outright.

    _copy_and_hash cleans up on exception, but a hard kill raises nothing --
    and on this machine the drive being read was causing
    DRIVER_POWER_STATE_FAILURE bugchecks, so hard kills happened. Nothing
    reconciled the leftovers and 64 accumulated, 1.34 GB of fully-written
    clips whose sources had since been cleared. All 64 turned out to be
    complete files that had simply never been renamed.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.library = self.root / "Footage" / "SentryClips" / "2026-05-14_14-56-27"
        self.library.mkdir(parents=True)

    def _write_valid_mp4(self, path: Path, payload: bytes = b"x" * 512) -> None:
        # Two well-formed boxes whose lengths land exactly on end-of-file.
        ftyp = (8 + 8).to_bytes(4, "big") + b"ftyp" + b"isom" + b"\0\0\0\0"
        mdat = (8 + len(payload)).to_bytes(4, "big") + b"mdat" + payload
        path.write_bytes(ftyp + mdat)

    def test_a_complete_partial_is_promoted_when_its_source_is_gone(self) -> None:
        partial = self.library / "front.mp4.mimir-partial"
        self._write_valid_mp4(partial)

        report = footage_import.reconcile_partial_copies(self.root / "Footage")

        self.assertEqual(report["recovered"], 1)
        self.assertTrue((self.library / "front.mp4").is_file())
        self.assertFalse(partial.exists())
        self.assertTrue(
            any("could not be checked against its original" in w for w in report["warnings"]),
            "recovering an unverified file must say that it was unverified",
        )

    def test_a_partial_beside_a_finished_file_is_just_deleted(self) -> None:
        self._write_valid_mp4(self.library / "front.mp4")
        partial = self.library / "front.mp4.mimir-partial"
        self._write_valid_mp4(partial)

        report = footage_import.reconcile_partial_copies(self.root / "Footage")

        self.assertEqual(report["deleted"], 1)
        self.assertEqual(report["recovered"], 0)
        self.assertTrue((self.library / "front.mp4").is_file())
        self.assertFalse(partial.exists())

    def test_a_truncated_partial_is_never_promoted(self) -> None:
        # A box header claiming more bytes than the file holds.
        partial = self.library / "front.mp4.mimir-partial"
        partial.write_bytes((9999).to_bytes(4, "big") + b"mdat" + b"only a few bytes")

        report = footage_import.reconcile_partial_copies(self.root / "Footage")

        self.assertEqual(report["recovered"], 0)
        self.assertFalse((self.library / "front.mp4").exists())
        self.assertTrue(partial.exists(), "truncated data was thrown away instead of kept for inspection")
        self.assertEqual(len(report["unrecoverable"]), 1)

    def test_an_import_reconciles_before_copying(self) -> None:
        """Otherwise the size check could mistake a stale partial for progress."""

        source_root = self.root / "TeslaCam" / "SentryClips" / "2026-05-14_14-56-27"
        source_root.mkdir(parents=True)
        (source_root / "front.mp4").write_bytes(b"real" * 500)
        stale = self.library / "orphan.mp4.mimir-partial"
        self._write_valid_mp4(stale)

        report = footage_import.import_footage(
            self.root / "TeslaCam", self.root / "Footage", remove_source=False, dry_run=False
        )

        self.assertTrue(report["ok"], report["failures"])
        self.assertEqual(report["partials_recovered"], 1)
        self.assertFalse(stale.exists())


if __name__ == "__main__":
    unittest.main()
