"""Tests for mimir_training_ground.py's sync bookkeeping and feedback intake.

Uses a fake S3 client rather than a real R2 bucket -- there is no live R2
account to test against yet (Phase 0 of the networked-submission plan is an
external step only the developer can do). What's tested here is everything
that doesn't require one: the download/dedup logic against
scripts/dev_intake_mock.py-style object keys, and the full feedback intake
path using a real encrypted package built with a throwaway test keypair.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import mimir_training_ground as tg
from mimir_core_v2.dataset_package import DatasetPackageError, find_age_executable
from mimir_core_v2.feedback_package import build_feedback_package, encrypt_feedback_collection


def _age_available() -> bool:
    try:
        find_age_executable()
        return True
    except DatasetPackageError:
        return False


class FakeS3Client:
    """Mimics just the two boto3 S3 client methods sync_command uses.

    `objects` maps object key -> local source file path, standing in for
    what's "in the bucket." Pagination is exercised by capping each response
    to `page_size` entries regardless of how many objects exist.
    """

    def __init__(self, objects: dict[str, Path], page_size: int = 2) -> None:
        self.objects = objects
        self.page_size = page_size
        self.download_calls: list[tuple[str, str]] = []

    def list_objects_v2(self, Bucket: str, Prefix: str, ContinuationToken: str | None = None) -> dict:  # noqa: N803
        matching = sorted(key for key in self.objects if key.startswith(Prefix))
        start = int(ContinuationToken) if ContinuationToken else 0
        page = matching[start : start + self.page_size]
        truncated = start + self.page_size < len(matching)
        return {
            "Contents": [{"Key": key} for key in page],
            "IsTruncated": truncated,
            "NextContinuationToken": str(start + self.page_size) if truncated else None,
        }

    def download_file(self, Bucket: str, Key: str, Filename: str) -> None:  # noqa: N803
        self.download_calls.append((Key, Filename))
        Path(Filename).write_bytes(self.objects[Key].read_bytes())


class ListNewObjectsTest(unittest.TestCase):
    def test_paginates_and_excludes_already_downloaded(self) -> None:
        objects = {f"contributions/2026/07/pkg{i}.mimir-dataset.age": Path(".") for i in range(5)}
        client = FakeS3Client(objects, page_size=2)

        already_seen = {"contributions/2026/07/pkg2.mimir-dataset.age"}
        result = tg.list_new_objects(client, "bucket", "contributions/", already_seen)

        expected = sorted(set(objects) - already_seen)
        self.assertEqual(result, expected)


class DownloadPhaseTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.dataset_root = self.root / "dataset"
        self.inbox = self.root / "inbox"
        self.feedback_downloads = self.root / "feedback" / "_downloads"

        self.fixture = self.root / "fixture.bin"
        self.fixture.write_bytes(b"age-encryption.org/v1\nnot real, just a fixture")

    def tearDown(self) -> None:
        self._temp.cleanup()

    # Dummy but present: R2Config.validate() only checks these are non-empty,
    # never that they're real. The FakeS3Client replaces the actual network
    # call, so a real credential is never needed for these tests.
    _DUMMY_R2_ENV = {
        "MIMIR_R2_ENDPOINT": "https://example.invalid",
        "MIMIR_R2_ACCESS_KEY_ID": "test-key",
        "MIMIR_R2_SECRET_ACCESS_KEY": "test-secret",
    }

    def _args(self) -> argparse.Namespace:
        return argparse.Namespace(
            dataset_root=str(self.dataset_root),
            inbox=str(self.inbox),
            feedback_inbox_downloads=str(self.feedback_downloads),
            r2_endpoint="",
            r2_bucket="",
        )

    def test_downloads_route_to_the_correct_folder_by_prefix(self) -> None:
        objects = {
            "contributions/2026/07/aaaa.mimir-dataset.age": self.fixture,
            "feedback/2026/07/bbbb.mimir-feedback.age": self.fixture,
        }
        client = FakeS3Client(objects)
        args = self._args()

        with mock.patch.dict(os.environ, self._DUMMY_R2_ENV), mock.patch.object(tg, "r2_client", lambda config: client):
            new_contributions, new_feedback = tg.download_phase(args)

        self.assertEqual(len(new_contributions), 1)
        self.assertEqual(len(new_feedback), 1)
        self.assertTrue((self.inbox / "aaaa.mimir-dataset.age").is_file())
        self.assertTrue((self.feedback_downloads / "bbbb.mimir-feedback.age").is_file())

    def test_a_second_sync_does_not_redownload_the_same_objects(self) -> None:
        objects = {"contributions/2026/07/aaaa.mimir-dataset.age": self.fixture}
        client = FakeS3Client(objects)
        args = self._args()

        with mock.patch.dict(os.environ, self._DUMMY_R2_ENV), mock.patch.object(tg, "r2_client", lambda config: client):
            first_contributions, _ = tg.download_phase(args)
            second_contributions, _ = tg.download_phase(args)

        self.assertEqual(len(first_contributions), 1)
        self.assertEqual(len(second_contributions), 0, "already-downloaded objects must not be pulled again")
        self.assertEqual(len(client.download_calls), 1)

    def test_missing_r2_credentials_fail_with_a_clear_message_not_a_crash(self) -> None:
        args = self._args()
        blanked = {key: "" for key in self._DUMMY_R2_ENV}
        with mock.patch.dict(os.environ, blanked):
            with self.assertRaises(SystemExit) as context:
                tg.download_phase(args)
        self.assertIn("Missing R2 credentials", str(context.exception))


@unittest.skipUnless(_age_available(), "official age tooling is not installed")
class FeedbackIntakeIntegrationTest(unittest.TestCase):
    """The one part of sync that DOES decrypt something -- proven with a real
    encrypted package and a throwaway keypair, not the fake client above."""

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)

        keygen = find_age_executable("age-keygen.exe")
        self.identity = self.root / "identity.txt"
        result = subprocess.run([str(keygen), "-o", str(self.identity)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        recipient_line = next(
            line for line in self.identity.read_text(encoding="utf-8").splitlines() if line.startswith("# public key:")
        )
        self.recipient = recipient_line.split(":", 1)[1].strip()

    def tearDown(self) -> None:
        self._temp.cleanup()

    def test_intake_feedback_phase_decrypts_and_files_a_real_package(self) -> None:
        staging = self.root / "staging"
        staging.mkdir()
        collection = build_feedback_package(
            {"incident_id": "incident_1", "user_selected_feedback": "Correct"}, None, staging
        )
        downloads = self.root / "downloads"
        downloads.mkdir()
        package_path = downloads / "test.mimir-feedback.age"
        encrypt_feedback_collection(collection, package_path, self.recipient)

        feedback_inbox = self.root / "feedback_inbox"
        tg.intake_feedback_phase(downloads, feedback_inbox, self.identity)

        filed = list(feedback_inbox.glob("*/feedback.json"))
        self.assertEqual(len(filed), 1)
        self.assertIn("incident_1", filed[0].read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
