"""Regression checks for consent, annotation, and candidate-model gates."""

from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from mimir_core_v2.dataset_package import DatasetPackageError, _safe_extract, exclusion_hashes
from mimir_core_v2.temporal_training import ExtractedSequence, FEATURE_NAMES, probability_timing
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


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(DataPipelineTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print("DATA PIPELINE OK" if result.wasSuccessful() else "DATA PIPELINE FAILED")
    raise SystemExit(0 if result.wasSuccessful() else 1)
