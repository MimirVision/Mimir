"""The Recycle Bin path has to be exercised against the real shell API.

Mocking SHFileOperationW would test the mock. The two things most likely to be
wrong here -- the extended-length prefix and the double-NUL terminator on
pFrom -- both fail only against the real call, and both fail in ways that look
like something else (a permissions error, a garbage return code).

Files are created under the system temp directory and sent to the bin, so
these tests put real entries in the developer's Recycle Bin. They are tiny and
named obviously.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from mimir_core_v2 import recycle_bin


class ExtendedPrefixTest(unittest.TestCase):
    """Pure string handling, so it runs everywhere."""

    def test_plain_paths_are_untouched(self) -> None:
        self.assertEqual(recycle_bin._strip_extended_prefix(r"C:\a\b.mp4"), r"C:\a\b.mp4")

    def test_extended_prefix_is_removed(self) -> None:
        # Mimir's own scanner writes paths in this shape -- every camera_clip
        # path in a real session.json carries it.
        self.assertEqual(
            recycle_bin._strip_extended_prefix(r"\\?\D:\TeslaCam\SentryClips\2026-05-14\front.mp4"),
            r"D:\TeslaCam\SentryClips\2026-05-14\front.mp4",
        )

    def test_extended_unc_prefix_becomes_a_normal_unc_path(self) -> None:
        self.assertEqual(
            recycle_bin._strip_extended_prefix(r"\\?\UNC\server\share\clip.mp4"),
            r"\\server\share\clip.mp4",
        )


@unittest.skipUnless(sys.platform == "win32", "The Recycle Bin is a Windows feature.")
class RecycleBinTest(unittest.TestCase):
    def test_a_file_is_removed_from_disk(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            target = Path(raw) / "mimir-recycle-bin-test.mp4"
            target.write_bytes(b"not really a video")

            results = recycle_bin.send_to_recycle_bin([target])

            self.assertEqual(len(results), 1)
            self.assertTrue(results[0].ok, results[0].reason)
            self.assertFalse(
                target.exists(),
                "The file is still on disk, so nothing was recycled and the caller would "
                "report freed space that was never freed.",
            )

    def test_a_missing_file_is_reported_as_already_gone_rather_than_failing(self) -> None:
        # Deleting the same incident twice, or deleting one whose files a user
        # already removed by hand, must not read as an error.
        with tempfile.TemporaryDirectory() as raw:
            results = recycle_bin.send_to_recycle_bin([Path(raw) / "never-existed.mp4"])

            self.assertTrue(results[0].ok)
            self.assertEqual(results[0].reason, "already gone")

    def test_a_folder_is_removed_with_everything_in_it(self) -> None:
        # An event folder is the unit people think they are deleting: the
        # clips, Tesla's event.json, and its thumbnail all go together.
        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw) / "2026-05-14_14-56-27"
            folder.mkdir()
            (folder / "front.mp4").write_bytes(b"clip")
            (folder / "event.json").write_text("{}", encoding="utf-8")
            (folder / "thumb.png").write_bytes(b"png")

            results = recycle_bin.send_to_recycle_bin([folder])

            self.assertTrue(results[0].ok, results[0].reason)
            self.assertFalse(folder.exists())

    def test_each_path_gets_its_own_result(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            present = Path(raw) / "present.mp4"
            present.write_bytes(b"clip")
            absent = Path(raw) / "absent.mp4"

            results = recycle_bin.send_to_recycle_bin([present, absent])

            self.assertEqual([r.ok for r in results], [True, True])
            self.assertEqual([Path(r.path).name for r in results], ["present.mp4", "absent.mp4"])


if __name__ == "__main__":
    unittest.main()
