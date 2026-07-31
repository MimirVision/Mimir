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
import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import mimir_training_ground as tg
from mimir_core_v2.dataset_package import DatasetPackageError, find_age_executable
from mimir_core_v2.feedback_package import build_feedback_package, encrypt_feedback_collection


def _run_and_capture_json(command: object) -> dict:
    """Runs a *_command function, returns the JSON it printed.

    Most `--json` commands print pretty-printed (multi-line) JSON as their
    entire output, so the whole buffer IS the payload -- try that first.
    `sync` is the exception: it interleaves human progress lines with one
    final single-line `MIMIR_SYNC_RESULT_JSON: {...}` marker, so fall back
    to finding that line specifically.
    """
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        assert callable(command)
        command()
    output = buffer.getvalue()
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        pass
    for line in reversed(output.splitlines()):
        if line.startswith("MIMIR_SYNC_RESULT_JSON: "):
            return json.loads(line[len("MIMIR_SYNC_RESULT_JSON: "):])
    raise AssertionError(f"could not parse JSON from output:\n{output}")


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


class StatusJsonTest(unittest.TestCase):
    """status --json exists for Mimir Forge (the GUI companion app) to
    consume without scraping the progress-bar text."""

    def test_json_output_matches_gate_progress_shape(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            args = argparse.Namespace(dataset_root=root, json=True)
            payload = _run_and_capture_json(lambda: tg.status_command(args))
            self.assertIn("pilot_gate_met", payload)
            self.assertEqual(payload["targets"], {"groups": 100, "positives": 25, "hard_negatives": 25})


class FeedbackJsonTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.feedback_inbox = Path(self._temp.name) / "feedback_inbox"
        entry = self.feedback_inbox / "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        entry.mkdir(parents=True)
        (entry / "feedback.json").write_text(
            json.dumps({"incident_id": "incident_1", "user_selected_feedback": "Correct", "timestamp": "t"}),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self._temp.cleanup()

    def test_list_json_wraps_items_with_package_id(self) -> None:
        args = argparse.Namespace(feedback_inbox=str(self.feedback_inbox), json=True)
        payload = _run_and_capture_json(lambda: tg.feedback_list_command(args))
        self.assertEqual(len(payload["items"]), 1)
        self.assertEqual(payload["items"][0]["package_id"], "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
        self.assertEqual(payload["items"][0]["feedback"]["incident_id"], "incident_1")

    def test_show_json_includes_empty_video_path_when_none_attached(self) -> None:
        args = argparse.Namespace(package_id="aaaaaaaa", feedback_inbox=str(self.feedback_inbox), json=True)
        payload = _run_and_capture_json(lambda: tg.feedback_show_command(args))
        self.assertEqual(payload["video_path"], "")
        self.assertEqual(payload["feedback"]["incident_id"], "incident_1")


class CollectionsCommandTest(unittest.TestCase):
    """Builds an intake_registry.json + collections/{id}/ tree directly --
    collections list/show only ever read already-intaken files, so this
    doesn't need a real encrypted package the way feedback intake tests do.
    """

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.dataset_root = Path(self._temp.name)
        self.package_id = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

        record = {
            "schema_version": "mimir_cvat_import_record_v1",
            "package_id": self.package_id,
            "imported_at": "2026-07-31T00:00:00Z",
            "split": "train",
            "cvat_project_id": 7,
            "cvat_tasks": [{"task_id": 42, "name": "train | bbbbbbbb | incident_1"}],
            "cvat_status": "complete",
            "cvat_error": None,
            "duplicate_media_rejected": 0,
        }
        (self.dataset_root / "intake_registry.json").write_text(
            json.dumps({"packages": {self.package_id: record}}), encoding="utf-8"
        )
        collection_dir = self.dataset_root / "collections" / self.package_id
        collection_dir.mkdir(parents=True)
        (collection_dir / "intake_record.json").write_text(json.dumps(record), encoding="utf-8")
        (collection_dir / "consent.json").write_text(
            json.dumps({"recorded_by": "tester", "rights_basis": "owned", "permission_reference": "self"}),
            encoding="utf-8",
        )
        (collection_dir / "manifest.json").write_text(
            json.dumps({"items": [{"incident_id": "incident_1", "split": "train"}]}), encoding="utf-8"
        )

    def tearDown(self) -> None:
        self._temp.cleanup()

    def test_list_json_summarizes_every_collection(self) -> None:
        args = argparse.Namespace(dataset_root=str(self.dataset_root), json=True)
        payload = _run_and_capture_json(lambda: tg.collections_list_command(args))
        self.assertEqual(len(payload["items"]), 1)
        item = payload["items"][0]
        self.assertEqual(item["package_id"], self.package_id)
        self.assertEqual(item["cvat_status"], "complete")
        self.assertEqual(item["cvat_task_count"], 1)

    def test_show_includes_consent_summary_and_item_count(self) -> None:
        args = argparse.Namespace(
            package_id=self.package_id[:8], dataset_root=str(self.dataset_root),
            cvat_url="", cvat_token="", cvat_token_file="",
        )
        payload = _run_and_capture_json(lambda: tg.collections_show_command(args))
        self.assertEqual(payload["package_id"], self.package_id)
        self.assertEqual(payload["item_count"], 1)
        self.assertEqual(payload["consent"]["recorded_by"], "tester")
        self.assertEqual(payload["live_cvat_tasks"], [], "no CVAT URL given -- must not attempt a network call")

    def test_show_fetches_live_cvat_status_when_url_given(self) -> None:
        args = argparse.Namespace(
            package_id=self.package_id, dataset_root=str(self.dataset_root),
            cvat_url="http://localhost:8080", cvat_token="test-token", cvat_token_file="",
        )

        class FakeCvatClient:
            def __init__(self, base_url: str, token: str) -> None:
                pass

            def task(self, task_id: int) -> dict:
                return {"status": "annotation", "size": 12}

        with mock.patch("mimir_core_v2.cvat_client.CvatClient", FakeCvatClient):
            payload = _run_and_capture_json(lambda: tg.collections_show_command(args))

        self.assertEqual(payload["live_cvat_tasks"], [{"task_id": 42, "name": "train | bbbbbbbb | incident_1", "status": "annotation", "size": 12}])

    def test_show_degrades_gracefully_when_cvat_is_unreachable(self) -> None:
        from mimir_core_v2.cvat_client import CvatError

        args = argparse.Namespace(
            package_id=self.package_id, dataset_root=str(self.dataset_root),
            cvat_url="http://localhost:8080", cvat_token="test-token", cvat_token_file="",
        )

        class FailingCvatClient:
            def __init__(self, base_url: str, token: str) -> None:
                pass

            def task(self, task_id: int) -> dict:
                raise CvatError("CVAT is unavailable")

        with mock.patch("mimir_core_v2.cvat_client.CvatClient", FailingCvatClient):
            payload = _run_and_capture_json(lambda: tg.collections_show_command(args))

        # The command must still return the rest of the data, not abort --
        # a CVAT outage degrades one field, not the whole command.
        self.assertEqual(payload["item_count"], 1)
        self.assertEqual(payload["live_cvat_tasks"], [{"task_id": 42, "name": "train | bbbbbbbb | incident_1", "error": "CVAT is unavailable"}])

    def test_ambiguous_id_prefix_is_rejected(self) -> None:
        second_id = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbc"
        registry_path = self.dataset_root / "intake_registry.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        registry["packages"][second_id] = dict(registry["packages"][self.package_id], package_id=second_id)
        registry_path.write_text(json.dumps(registry), encoding="utf-8")

        args = argparse.Namespace(
            package_id="bbbbbbbb", dataset_root=str(self.dataset_root),
            cvat_url="", cvat_token="", cvat_token_file="",
        )
        exit_code = tg.collections_show_command(args)
        self.assertEqual(exit_code, 1)


class SyncResultMarkerTest(unittest.TestCase):
    """sync always emits one final MIMIR_SYNC_RESULT_JSON: line alongside its
    normal progress output -- built for Mimir Forge, which needs the outcome
    without scraping the interleaved prose above it."""

    _DUMMY_R2_ENV = {
        "MIMIR_R2_ENDPOINT": "https://example.invalid",
        "MIMIR_R2_ACCESS_KEY_ID": "test-key",
        "MIMIR_R2_SECRET_ACCESS_KEY": "test-secret",
    }

    def test_marker_line_reports_zero_new_items_on_an_empty_bucket(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            args = argparse.Namespace(
                dataset_root=str(root_path),
                inbox=str(root_path / "inbox"),
                feedback_inbox=str(root_path / "feedback_inbox"),
                feedback_inbox_downloads=str(root_path / "feedback_inbox" / "_downloads"),
                identity=str(root_path / "identity.txt"),
                r2_endpoint="",
                r2_bucket="",
                create_cvat_tasks=False,
                cvat_url="",
                cvat_token="",
                cvat_token_file="",
            )
            client = FakeS3Client({})
            with mock.patch.dict(os.environ, self._DUMMY_R2_ENV), mock.patch.object(tg, "r2_client", lambda config: client):
                payload = _run_and_capture_json(lambda: tg.sync_command(args))

        self.assertEqual(payload["schema_version"], "mimir_sync_result_v1")
        self.assertEqual(payload["new_contribution_count"], 0)
        self.assertEqual(payload["new_feedback_count"], 0)
        self.assertIsNone(payload["contribution_intake_exit_code"])
        self.assertEqual(payload["contribution_results"], [])
        self.assertEqual(payload["feedback_results"], [])
        self.assertIn("pilot_gate_met", payload["gate_progress"])

    def test_marker_line_reports_a_failed_contribution_by_name_and_reason(self) -> None:
        # Mirrors what actually happened in production: a stray non-age file
        # landed in the bucket, sync downloaded it, intake correctly rejected
        # it -- and Forge needed to show *which* file and *why*, not just a
        # bare exit code. contribution_results is what makes that possible.
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            fixture = root_path / "fixture.bin"
            fixture.write_bytes(b"not a real age package")
            identity = root_path / "identity.txt"
            identity.write_text("placeholder -- decryption fails before this is read", encoding="utf-8")

            args = argparse.Namespace(
                dataset_root=str(root_path / "dataset"),
                inbox=str(root_path / "inbox"),
                feedback_inbox=str(root_path / "feedback_inbox"),
                feedback_inbox_downloads=str(root_path / "feedback_inbox" / "_downloads"),
                identity=str(identity),
                r2_endpoint="",
                r2_bucket="",
                create_cvat_tasks=False,
                cvat_url="",
                cvat_token="",
                cvat_token_file="",
            )
            client = FakeS3Client({"contributions/2026/07/bad.mimir-dataset.age": fixture})
            with mock.patch.dict(os.environ, self._DUMMY_R2_ENV), mock.patch.object(tg, "r2_client", lambda config: client):
                payload = _run_and_capture_json(lambda: tg.sync_command(args))

        self.assertEqual(payload["new_contribution_count"], 1)
        self.assertEqual(len(payload["contribution_results"]), 1)
        result = payload["contribution_results"][0]
        self.assertEqual(result["file"], "bad.mimir-dataset.age")
        self.assertEqual(result["status"], "failed")
        self.assertIn("error", result)
        self.assertTrue(result["error"])


if __name__ == "__main__":
    unittest.main()
