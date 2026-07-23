"""Regression checks for consent, annotation, and candidate-model gates."""

from __future__ import annotations

import json
import hashlib
import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from mimir_core_v2.dataset_package import DatasetPackageError, _safe_extract, exclusion_hashes
from mimir_core_v2.temporal_training import ExtractedSequence, FEATURE_NAMES, probability_timing
from mimir_stationary_data import (
    DOOR_ACTIVITY_LABELS,
    assign_source_isolated_splits,
    download_record,
    select_pilot_records,
    source_record,
)
from mimir_core_v2_training import audit_dataset, build_parser


ROOT = Path(__file__).resolve().parent


class DataPipelineTests(unittest.TestCase):
    def test_versioned_schemas_are_valid_json_objects(self) -> None:
        expected = {
            "contribution_package_v1.schema.json",
            "consent_receipt_v2.schema.json",
            "cvat_import_record_v1.schema.json",
            "annotation_export_v1.schema.json",
            "training_run_v1.schema.json",
            "candidate_model_manifest_v1.schema.json",
            "evaluation_report_v1.schema.json",
            "external_dataset_receipt_v1.schema.json",
            "stationary_auxiliary_manifest_v1.schema.json",
            "otw_auxiliary_manifest_v1.schema.json",
            "carla_synthetic_manifest_v1.schema.json",
        }
        actual = {path.name for path in (ROOT / "schemas").glob("*.schema.json")}
        self.assertTrue(expected.issubset(actual))
        for name in expected:
            payload = json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))
            self.assertEqual(payload.get("type"), "object")
            self.assertIn("$schema", payload)

    def test_cvat_definition_has_required_labels_and_policy(self) -> None:
        payload = json.loads((ROOT / "cvat_project_v1.json").read_text(encoding="utf-8"))
        labels = {str(item.get("name")) for item in payload.get("labels", [])}
        self.assertTrue({"ego_vehicle", "person", "vehicle", "vehicle_door", "event_outcome"}.issubset(labels))
        self.assertTrue(payload["annotation_policy"]["pixel_overlap_is_not_physical_proof"])
        self.assertEqual(payload["annotation_policy"]["blind_relabel_fraction"], 0.1)

    def test_regression_exclusions_are_hash_based(self) -> None:
        exclusions = exclusion_hashes()
        self.assertGreaterEqual(len(exclusions), 3)
        for digest, reason in exclusions.items():
            self.assertEqual(len(digest), 64)
            self.assertNotIn("reddit", reason.lower())

    def test_zip_path_traversal_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "unsafe.zip"
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr("../outside.txt", "not allowed")
            with self.assertRaises(DatasetPackageError):
                _safe_extract(archive, root / "output")

    def test_empty_dataset_never_unlocks_training(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report = audit_dataset(Path(temporary))
        self.assertFalse(report["passed"])
        self.assertFalse(report["training_ready"])
        self.assertIn("no consented dataset collections were found", report["errors"])

    def test_temporal_contract_reports_probability_and_uncertainty(self) -> None:
        self.assertGreaterEqual(len(FEATURE_NAMES), 10)
        result = probability_timing([0.0, 0.5, 1.0], [0.1, 0.9, 0.2])
        self.assertEqual(result["best_time_sec"], 0.5)
        self.assertEqual(result["probability"], 0.9)
        self.assertIsNotNone(result["timing_uncertainty_sec"])

    def test_external_timing_has_separate_event_target(self) -> None:
        self.assertIn("event_time_sec", ExtractedSequence.__annotations__)
        self.assertIn("event_target", ExtractedSequence.__annotations__)
        self.assertIn("alert_time_sec", ExtractedSequence.__annotations__)
        self.assertIn("time_to_accident_sec", ExtractedSequence.__annotations__)
        args = build_parser().parse_args(
            [
                "prepare-nexar",
                "--source-root",
                "C:/licensed-source",
                "--output",
                "C:/prepared",
            ]
        )
        self.assertEqual(args.command, "prepare-nexar")
        pretrain = build_parser().parse_args(
            [
                "pretrain-temporal",
                "--features",
                "C:/features",
                "--prepared",
                "C:/prepared",
            ]
        )
        self.assertEqual(pretrain.command, "pretrain-temporal")

    def test_meva_door_activity_is_auxiliary_not_contact_truth(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            activity = root / "2018-03-12.11-05-01.11-10-01.school.G328.activities.yml"
            activity.write_text(
                "[{act: {act2: !!set {person_opens_vehicle_door: null}}}]",
                encoding="utf-8",
            )
            record = source_record(
                activity,
                "train_auxiliary",
                {"person_opens_vehicle_door": 1},
            )
            self.assertEqual(record["role"], "door_activity")
            self.assertEqual(record["door_activity_labels"], ["person_opens_vehicle_door"])
            self.assertTrue(set(record["door_activity_labels"]).issubset(DOOR_ACTIVITY_LABELS))
            self.assertNotIn("contact", record["role"])

        registry = json.loads((ROOT / "dataset_sources.json").read_text(encoding="utf-8"))
        meva = next(item for item in registry["sources"] if item["id"] == "meva_kf1_stationary_auxiliary")
        self.assertEqual(meva["license"], "CC-BY-4.0")
        self.assertFalse(meva["promotion_eligible"])
        self.assertFalse(meva["contact_ground_truth_available"])

    def test_meva_pilot_selection_is_bounded_and_balanced(self) -> None:
        records = []
        for split in ("train_auxiliary", "validation_auxiliary"):
            for role in ("door_activity", "hard_negative", "suspicious_activity"):
                for index in range(4):
                    records.append(
                        {
                            "split": split,
                            "role": role,
                            "source_url": f"https://example.invalid/{split}/{role}/{index}.avi",
                            "video_name": f"{split}-{role}-{index}.avi",
                            "expected_bytes": 100,
                        }
                    )

        selected = select_pilot_records(records, 1_000)
        self.assertLessEqual(sum(item["expected_bytes"] for item in selected), 1_000)
        self.assertTrue(any(item["role"] == "door_activity" for item in selected))
        self.assertTrue(any(item["role"] == "hard_negative" for item in selected))
        self.assertTrue(any(item["split"] == "validation_auxiliary" for item in selected))
        self.assertEqual(
            [item["source_url"] for item in selected],
            [item["source_url"] for item in select_pilot_records(records, 1_000)],
        )

    def test_meva_download_resumes_part_and_validates_checksum(self) -> None:
        payload = b"0123456789"
        digest = hashlib.sha256(payload).hexdigest()

        class Response(io.BytesIO):
            status = 206
            headers = {"Content-Range": "bytes 4-9/10", "ETag": '"stable-etag"'}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                self.close()

            def getcode(self):
                return self.status

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            record = {
                "split": "train_auxiliary",
                "video_name": "resume.avi",
                "source_url": "https://example.invalid/resume.avi",
                "expected_bytes": len(payload),
                "expected_sha256": digest,
                "etag": "stable-etag",
            }
            part = output / "train_auxiliary" / "resume.avi.part"
            part.parent.mkdir(parents=True)
            part.write_bytes(payload[:4])

            with mock.patch(
                "mimir_stationary_data.urllib.request.urlopen",
                return_value=Response(payload[4:]),
            ) as urlopen:
                result = download_record(record, output, timeout=2, retries=0)

            request = urlopen.call_args.args[0]
            self.assertEqual(request.headers.get("Range"), "bytes=4-")
            self.assertEqual((output / "train_auxiliary" / "resume.avi").read_bytes(), payload)
            self.assertFalse(part.exists())
            self.assertEqual(result["sha256"], digest)
            self.assertTrue(result["downloaded"])

    def test_meva_split_assignment_keeps_all_cameras_together(self) -> None:
        records = [
            {
                "source_event_group": "same-physical-event",
                "video_name": "front.avi",
                "split": "train_auxiliary",
            },
            {
                "source_event_group": "same-physical-event",
                "video_name": "rear.avi",
                "split": "validation_auxiliary",
            },
        ]
        assigned = assign_source_isolated_splits(records)
        self.assertEqual(len({record["split"] for record in assigned}), 1)
        self.assertEqual({record["source_split"] for record in assigned}, {"train_auxiliary", "validation_auxiliary"})
        self.assertTrue(all(record["split_assignment_version"] for record in assigned))


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(DataPipelineTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print("DATA PIPELINE OK" if result.wasSuccessful() else "DATA PIPELINE FAILED")
    raise SystemExit(0 if result.wasSuccessful() else 1)
