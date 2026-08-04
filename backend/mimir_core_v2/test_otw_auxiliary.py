"""Focused tests for licensed OTW auxiliary preparation and safety policy."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from .otw_auxiliary import (
    CLASS_NAMES,
    FRAME_SAMPLES,
    INPUT_CHANNELS,
    OtwTrainingError,
    build_cache,
    interpolate_box,
    sample_frame_indices,
)
from mimir_otw_data import class_for_label, split_for_video


class OtwAuxiliaryTests(unittest.TestCase):
    def test_label_policy_separates_door_from_contact(self) -> None:
        self.assertEqual(class_for_label("opening door"), "side_door_activity")
        self.assertEqual(class_for_label("closing trunk"), "other_vehicle_access")
        self.assertEqual(class_for_label("talking on phone"), "other_activity")
        self.assertIsNone(class_for_label("impact"))

    def test_split_is_stable_and_source_isolated(self) -> None:
        first = split_for_video("homes", "00001")
        self.assertEqual(first, split_for_video("homes", "00001"))
        self.assertIn(first, {"train_auxiliary", "validation_auxiliary"})

    def test_temporal_frame_sampling_includes_boundaries(self) -> None:
        samples = sample_frame_indices(10, 40)
        self.assertEqual(len(samples), FRAME_SAMPLES)
        self.assertEqual(samples[0], 10)
        self.assertEqual(samples[-1], 40)
        self.assertEqual(samples, sorted(samples))

    def test_box_interpolation(self) -> None:
        box = interpolate_box([[0, 0, 10, 20, 30], [10, 10, 20, 30, 40]], 5)
        self.assertEqual(box, (5.0, 15.0, 25.0, 35.0))

    def test_model_contract_has_three_semantic_classes(self) -> None:
        self.assertEqual(
            CLASS_NAMES,
            ("side_door_activity", "other_vehicle_access", "other_activity"),
        )
        self.assertEqual(INPUT_CHANNELS, 15)

    def test_cache_build_refuses_a_manifest_that_claims_promotion_eligibility(self) -> None:
        # Mirrors the MEVA/CARLA promotion guards: build_cache is the mandatory first
        # stage of OTW auxiliary training, so this is where a tampered or regressed
        # manifest must be rejected before any training work happens.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "schema_version": "mimir_otw_auxiliary_manifest_v1",
                        "promotion_eligible": True,
                        "videos": [],
                        "activities": [],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(OtwTrainingError):
                build_cache(manifest_path, root / "cache.npz")


if __name__ == "__main__":
    unittest.main()
