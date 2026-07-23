"""Tests for licensed, non-promotable MEVA auxiliary pretraining."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from .meva_auxiliary import FEATURE_NAMES, extract_record_features, train_auxiliary


class MevaAuxiliaryTests(unittest.TestCase):
    def test_geometry_features_preserve_auxiliary_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            video = root / "clip.avi"
            video.write_bytes(b"not-a-real-video")
            types = root / "clip.types.yml"
            types.write_text(
                "- {'types': {'cset3': {'person': 1.0}, 'id1': 1}}\n"
                "- {'types': {'cset3': {'vehicle': 1.0}, 'id1': 2}}\n",
                encoding="utf-8",
            )
            geometry = root / "clip.geom.yml"
            geometry.write_text(
                "- {'geom': {'g0': '100 100 200 300', 'id1': 1, 'keyframe': True, 'ts0': 0}}\n"
                "- {'geom': {'g0': '180 120 500 400', 'id1': 2, 'keyframe': True, 'ts0': 0}}\n"
                "- {'geom': {'g0': '120 100 220 300', 'id1': 1, 'keyframe': True, 'ts0': 10}}\n",
                encoding="utf-8",
            )
            row = extract_record_features(
                {
                    "video_name": video.name,
                    "local_path": str(video),
                    "split": "train_auxiliary",
                    "role": "door_activity",
                    "source_event_group": "group-a",
                    "sha256": "a" * 64,
                    "annotation_files": {"types": str(types), "geometry": str(geometry)},
                }
            )
            self.assertEqual(row["target"], 1)
            self.assertEqual(row["feature_names"], list(FEATURE_NAMES))
            self.assertEqual(row["license"], "CC-BY-4.0")
            self.assertFalse(row["contact_ground_truth_available"])
            self.assertGreater(row["features"][0], 0)
            self.assertGreater(row["features"][1], 0)

    def test_shadow_trainer_uses_source_isolated_validation(self) -> None:
        rows = []
        for split in ("train_auxiliary", "validation_auxiliary"):
            for index in range(4):
                target = index % 2
                features = [float(target)] * len(FEATURE_NAMES)
                rows.append(
                    {
                        "source_event_group": f"{split}-{index}",
                        "video_name": f"{split}-{index}.avi",
                        "split": split,
                        "target": target,
                        "features": features,
                    }
                )
        result = train_auxiliary(rows, epochs=200)
        self.assertEqual(result["validation_metrics"]["count"], 4)
        self.assertEqual(len(result["weights"]), len(FEATURE_NAMES))
        self.assertGreaterEqual(result["validation_metrics"]["accuracy"], 0.5)


if __name__ == "__main__":
    unittest.main()
