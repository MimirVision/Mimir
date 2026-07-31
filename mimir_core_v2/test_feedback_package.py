"""Round-trip and rejection tests for the feedback packaging format.

feedback_package.py is deliberately independent from dataset_package.py (see
that module's docstring for why), so it gets its own test coverage rather
than inheriting dataset_package's. These tests use the real `age.exe` binary
against a throwaway keypair generated for the test run -- not the real
production identity, which never appears in this repo.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from .dataset_package import DatasetPackageError, find_age_executable
from .feedback_package import (
    FEEDBACK_PACKAGE_SCHEMA,
    build_feedback_package,
    encrypt_feedback_collection,
    intake_feedback_package,
)


def _age_available() -> bool:
    try:
        find_age_executable()
        return True
    except DatasetPackageError:
        return False


@unittest.skipUnless(_age_available(), "official age tooling is not installed")
class FeedbackPackageTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)

        import subprocess

        keygen = find_age_executable("age-keygen.exe")
        identity_path = self.root / "test_identity.txt"
        result = subprocess.run(
            [str(keygen), "-o", str(identity_path)],
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.identity = identity_path
        # age-keygen writes the recipient as a comment line inside the identity file.
        recipient_line = next(
            line for line in identity_path.read_text(encoding="utf-8").splitlines()
            if line.startswith("# public key:")
        )
        self.recipient = recipient_line.split(":", 1)[1].strip()

    def tearDown(self) -> None:
        self._temp.cleanup()

    def test_round_trip_without_video(self) -> None:
        feedback = {"incident_id": "incident_0001", "user_selected_feedback": "Correct", "notes": "Looks right."}
        staging = self.root / "staging"
        staging.mkdir()
        collection = build_feedback_package(feedback, None, staging)

        package_json = (collection / "package.json")
        self.assertTrue(package_json.is_file())

        output = self.root / "out.mimir-feedback.age"
        package = encrypt_feedback_collection(collection, output, self.recipient)
        self.assertTrue(output.is_file())

        feedback_root = self.root / "feedback_inbox"
        result = intake_feedback_package(output, self.identity, feedback_root)
        self.assertEqual(result["status"], "imported")
        # The package_id assigned at encryption time must be the same one
        # intake files it under -- a real check, unlike the tautological
        # self-comparison this replaced.
        self.assertEqual(package["package_id"], result["package_id"])

        imported_feedback = (feedback_root / result["package_id"] / "feedback.json")
        self.assertTrue(imported_feedback.is_file())
        self.assertIn("Looks right.", imported_feedback.read_text(encoding="utf-8"))

    def test_round_trip_with_video(self) -> None:
        video = self.root / "clip.mp4"
        video.write_bytes(b"not a real video, just bytes for the round trip")
        feedback = {"incident_id": "incident_0002", "user_selected_feedback": "Missed obvious event"}
        staging = self.root / "staging"
        staging.mkdir()
        collection = build_feedback_package(feedback, video, staging)
        self.assertTrue((collection / "video" / "clip.mp4").is_file())

        output = self.root / "out.mimir-feedback.age"
        encrypt_feedback_collection(collection, output, self.recipient)

        feedback_root = self.root / "feedback_inbox"
        result = intake_feedback_package(output, self.identity, feedback_root)
        self.assertEqual(result["status"], "imported")
        self.assertTrue((feedback_root / result["package_id"] / "video" / "clip.mp4").is_file())
        self.assertEqual(
            (feedback_root / result["package_id"] / "video" / "clip.mp4").read_bytes(),
            video.read_bytes(),
        )

    def test_repeated_intake_of_the_same_package_is_idempotent(self) -> None:
        feedback = {"incident_id": "incident_0003", "user_selected_feedback": "Correct"}
        staging = self.root / "staging"
        staging.mkdir()
        collection = build_feedback_package(feedback, None, staging)
        output = self.root / "out.mimir-feedback.age"
        encrypt_feedback_collection(collection, output, self.recipient)

        feedback_root = self.root / "feedback_inbox"
        first = intake_feedback_package(output, self.identity, feedback_root)
        second = intake_feedback_package(output, self.identity, feedback_root)
        self.assertEqual(first["status"], "imported")
        self.assertEqual(second["status"], "already_imported")
        self.assertEqual(first["package_id"], second["package_id"])

    def test_a_tampered_package_is_rejected_not_silently_corrupted(self) -> None:
        feedback = {"incident_id": "incident_0004", "user_selected_feedback": "Correct"}
        staging = self.root / "staging"
        staging.mkdir()
        collection = build_feedback_package(feedback, None, staging)
        output = self.root / "out.mimir-feedback.age"
        encrypt_feedback_collection(collection, output, self.recipient)

        tampered = self.root / "tampered.mimir-feedback.age"
        original = bytearray(output.read_bytes())
        # Flip a byte well past the age header so this exercises ciphertext
        # tampering, not just truncating the file.
        flip_at = min(len(original) - 1, 60)
        original[flip_at] ^= 0xFF
        tampered.write_bytes(bytes(original))

        with self.assertRaises(DatasetPackageError):
            intake_feedback_package(tampered, self.identity, self.root / "feedback_inbox")

    def test_output_filename_must_use_the_feedback_suffix(self) -> None:
        # Distinct from .mimir-dataset.age is deliberate: it's what keeps
        # mimir_core_v2_pipeline.py's contribution inbox scan from ever
        # picking up a feedback package by accident.
        feedback = {"incident_id": "incident_0005", "user_selected_feedback": "Correct"}
        staging = self.root / "staging"
        staging.mkdir()
        collection = build_feedback_package(feedback, None, staging)

        with self.assertRaises(DatasetPackageError):
            encrypt_feedback_collection(collection, self.root / "wrong.mimir-dataset.age", self.recipient)

    def test_encryption_rejects_a_malformed_recipient(self) -> None:
        feedback = {"incident_id": "incident_0006", "user_selected_feedback": "Correct"}
        staging = self.root / "staging"
        staging.mkdir()
        collection = build_feedback_package(feedback, None, staging)

        with self.assertRaises(DatasetPackageError):
            encrypt_feedback_collection(collection, self.root / "out.mimir-feedback.age", "not-a-real-recipient")

    def test_package_schema_tag_is_the_feedback_schema(self) -> None:
        feedback = {"incident_id": "incident_0007", "user_selected_feedback": "Correct"}
        staging = self.root / "staging"
        staging.mkdir()
        collection = build_feedback_package(feedback, None, staging)
        import json

        package = json.loads((collection / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(package["schema_version"], FEEDBACK_PACKAGE_SCHEMA)
        self.assertFalse(package["automatic_upload"])


if __name__ == "__main__":
    unittest.main()
