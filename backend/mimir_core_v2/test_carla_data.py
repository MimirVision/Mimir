from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import mimir_carla_data
import mimir_carla_generate
from mimir_core_v2.carla_auxiliary import CarlaAuxiliaryError, _choose_threshold, _classification_metrics, train_carla_auxiliary
from mimir_core_v2.temporal_training import _annotation_geometry


class CarlaDataTests(unittest.TestCase):
    def test_split_is_deterministic(self) -> None:
        value = mimir_carla_generate.split_for_scenario("scenario-17")
        self.assertEqual(value, mimir_carla_generate.split_for_scenario("scenario-17"))
        self.assertIn(value, {"train", "validation", "test"})

    def test_archive_destination_rejects_parent_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(mimir_carla_data.CarlaDataError):
                mimir_carla_data._safe_destination(root, "../outside.exe")

    def test_source_receipt_quarantines_synthetic_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            receipt = mimir_carla_data.source_receipt(Path(directory))
        self.assertFalse(receipt["promotion_eligible"])
        self.assertFalse(receipt["real_world_contact_ground_truth"])
        self.assertTrue(receipt["simulated_collision_ground_truth"])

    def test_prepared_verifier_rejects_missing_scenarios(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = root / "report.json"
            exit_code = mimir_carla_data.verify_prepared(
                SimpleNamespace(
                    root=str(root),
                    report=str(report),
                    expected_scenarios=1,
                    generator_version="mimir_carla_stationary_generator_v2",
                )
            )
            payload = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(exit_code, 1)
        self.assertFalse(payload["passed"])
        self.assertTrue(payload["failures"])

    def test_training_manifest_is_auxiliary_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = mimir_carla_generate.build_training_manifest(
                root,
                [
                    {
                        "scenario_id": "one",
                        "split": "train",
                        "duration_sec": 7.0,
                        "contact_outcome": "impact",
                        "impact_time_sec": 2.5,
                        "actual_collision": True,
                        "scenario_kind": "rear_impact",
                        "media": [{"path": str(root / "one.mp4")}],
                    },
                    {
                        "scenario_id": "two",
                        "split": "validation",
                        "duration_sec": 7.0,
                        "contact_outcome": "no_contact",
                        "impact_time_sec": None,
                        "actual_collision": False,
                        "scenario_kind": "rear_near_miss",
                        "media": [{"path": str(root / "two.mp4")}],
                    },
                ],
            )
        self.assertFalse(manifest["promotion_eligible"])
        self.assertFalse(manifest["real_world_evaluation_eligible"])
        self.assertEqual(manifest["source_audit"]["positive_items"], 1)
        self.assertEqual(manifest["source_audit"]["hard_negative_items"], 1)

    def test_instance_actor_ids_are_decoded_losslessly(self) -> None:
        try:
            import numpy as np
        except ImportError:
            self.skipTest("NumPy is unavailable")
        pixels = np.zeros((2, 2, 4), dtype=np.uint8)
        ego_id = 258
        target_id = 513
        pixels[0, 0, 0] = ego_id & 255
        pixels[0, 0, 1] = ego_id >> 8
        pixels[1, 1, 0] = target_id & 255
        pixels[1, 1, 1] = target_id >> 8
        image = SimpleNamespace(raw_data=pixels.tobytes(), width=2, height=2)
        ego_mask, target_mask, encoding = mimir_carla_generate.decode_actor_masks(
            image,
            ego_id,
            target_id,
        )
        self.assertEqual(encoding, "green_high_blue_low")
        self.assertTrue(ego_mask[0, 0])
        self.assertTrue(target_mask[1, 1])

    def test_threshold_selection_uses_validation_only_rows(self) -> None:
        rows = [
            {"target": 1, "score": 0.9},
            {"target": 1, "score": 0.8},
            {"target": 0, "score": 0.2},
            {"target": 0, "score": 0.1},
        ]
        threshold = _choose_threshold(rows)
        metrics = _classification_metrics(rows, threshold)
        self.assertEqual(metrics["false_positive"], 0)
        self.assertEqual(metrics["false_negative"], 0)

    def test_carla_mask_geometry_overrides_bounding_box_approximation(self) -> None:
        objects = [
            {
                "time_sec": 1.0,
                "class_name": "ego_vehicle",
                "bbox_xyxy": [0, 80, 100, 100],
            },
            {
                "time_sec": 1.0,
                "class_name": "vehicle",
                "bbox_xyxy": [20, 10, 80, 70],
                "pair_geometry_source": "carla_instance_actor_masks",
                "pair_mask_adjacency": True,
                "pair_mask_distance_norm": 0.0,
            },
        ]
        geometry, distance = _annotation_geometry(
            objects,
            time_sec=1.0,
            frame_width=100,
            frame_height=100,
            previous_distance=0.2,
            sample_step_sec=0.1,
        )
        self.assertEqual(geometry[0], 1.0)
        self.assertEqual(geometry[1], 0.0)
        self.assertEqual(distance, 0.0)

    def test_shadow_training_refuses_a_manifest_that_claims_promotion_eligibility(self) -> None:
        # Same safety net as the OTW/MEVA auxiliary guards: a tampered or regressed
        # training manifest must be rejected before any shadow-model training happens.
        # torch/numpy are stubbed so this test doesn't need the real GPU training
        # environment (requirements-training.txt) -- the guard fires before either
        # library's real functionality would ever be used.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepared = root / "prepared"
            prepared.mkdir()
            (prepared / "training_manifest.json").write_text(
                json.dumps(
                    {
                        "training_purpose": "synthetic_stationary_collision_timing_pretraining_only",
                        "promotion_eligible": True,
                    }
                ),
                encoding="utf-8",
            )
            fake_modules = {"torch": MagicMock(), "numpy": MagicMock()}
            with patch.dict(sys.modules, fake_modules):
                with self.assertRaises(CarlaAuxiliaryError):
                    train_carla_auxiliary(root / "features", prepared, root / "output")


if __name__ == "__main__":
    unittest.main()
