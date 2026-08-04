"""Focused tests for grouped-camera contact semantics."""

from __future__ import annotations

import copy
import unittest

from .evidence_extractor import _apply_group_contact_context, _impact_candidate_details


def _motion_sample(time_sec: float, motion: float, shake: float = 0.0) -> dict:
    return {
        "time_sec": time_sec,
        "motion_score": motion,
        "camera_shake_score": shake,
    }


def _side_door_evidence() -> dict:
    return {
        "motion_score": 0.08,
        "max_motion_score": 0.39,
        "localized_motion_score": 0.88,
        "motion_spike_ratio": 5.16,
        "camera_shake_score": 0.24,
        "abrupt_scene_change": False,
        "object_contact_candidate": True,
        "object_contact_used_for_contact": True,
        "object_contact_time_sec": 10.968,
        "object_touching_ego_vehicle": True,
        "contact_object_class": "person+vehicle",
        "contact_object_type": "vehicle_door_or_body",
        "impact_level": "HIGH",
        "contact_level": "HIGH",
        "possible_impact": True,
        "possible_contact": True,
        "strong_impact_like_motion": True,
        "hard_contact_candidate": True,
        "rear_impact_candidate": True,
        "crash_safety_triggered": False,
        "no_yolo_motion_impact_candidate": False,
        "impact_evidence_reasons": [
            "hard_close_vehicle_motion",
            "rear_impact_candidate",
        ],
        "contact_evidence_reasons": [
            "hard_close_vehicle_motion",
            "hard_contact_candidate",
        ],
        "impact_candidate_reasons": [
            "vehicle_detected",
            "hard_contact_candidate",
        ],
        "motion_samples": [_motion_sample(10.968, 0.39, 0.21)],
        "vehicle_detected": True,
        "person_detected": True,
    }


def _motion_only_hard_contact_evidence() -> dict:
    # hard_contact_candidate can also be reached with no bounding-box overlap
    # at all -- _impact_candidate_details() only needs a generically detected
    # vehicle (anywhere in frame, not necessarily touching anything) plus
    # motion/shake/spike thresholds. object_contact_candidate is deliberately
    # left False/absent here to reproduce that path.
    return {
        "max_motion_score": 0.7,
        "localized_motion_score": 0.6,
        "motion_spike_ratio": 6.0,
        "camera_shake_score": 0.6,
        "abrupt_scene_change": False,
        "object_contact_candidate": False,
        "vehicle_detected": True,
        "person_detected": False,
        "impact_level": "HIGH",
        "contact_level": "HIGH",
        "possible_impact": True,
        "possible_contact": True,
        "strong_impact_like_motion": True,
        "hard_contact_candidate": True,
        "rear_impact_candidate": False,
        "crash_safety_triggered": False,
        "no_yolo_motion_impact_candidate": False,
        "motion_spike_time_sec": 12.0,
        "motion_samples": [_motion_sample(12.0, 0.7, 0.6)],
    }


class GroupContactSemanticsTests(unittest.TestCase):
    def test_uncorroborated_side_door_is_review_evidence_not_hard_impact(self) -> None:
        cameras = {
            "front": {"motion_samples": [_motion_sample(10.971, 0.011)]},
            "back": {"motion_samples": [_motion_sample(10.720, 0.007)]},
            "left_repeater": {"motion_samples": [_motion_sample(10.969, 0.010)]},
            "right_repeater": _side_door_evidence(),
        }

        _apply_group_contact_context(cameras)
        evidence = cameras["right_repeater"]

        self.assertTrue(evidence["door_articulation_candidate"])
        self.assertTrue(evidence["single_camera_close_activity"])
        self.assertFalse(evidence["multi_camera_impact_corroborated"])
        self.assertFalse(evidence["hard_contact_candidate"])
        self.assertFalse(evidence["rear_impact_candidate"])
        self.assertFalse(evidence["strong_impact_like_motion"])
        self.assertEqual("MEDIUM", evidence["impact_level"])
        self.assertEqual("MEDIUM", evidence["contact_level"])
        self.assertFalse(evidence["object_contact_used_for_contact"])
        self.assertFalse(evidence["object_touching_ego_vehicle"])

    def test_synchronized_second_camera_preserves_hard_contact(self) -> None:
        side = _side_door_evidence()
        cameras = {
            "back": {"motion_samples": [_motion_sample(10.95, 0.6)]},
            "right_repeater": side,
        }

        _apply_group_contact_context(cameras)

        self.assertTrue(side["multi_camera_impact_corroborated"])
        self.assertEqual(["back"], side["multi_camera_impact_support_cameras"])
        self.assertFalse(side["door_articulation_candidate"])
        self.assertTrue(side["hard_contact_candidate"])
        self.assertEqual("HIGH", side["contact_level"])

    def test_uncorroborated_vehicle_overlap_is_close_activity(self) -> None:
        side = _side_door_evidence()
        side["contact_object_class"] = "vehicle"
        side["contact_object_type"] = "vehicle_body"
        cameras = {
            "front": {"motion_samples": [_motion_sample(4.02, 0.01)]},
            "left_repeater": side,
        }

        _apply_group_contact_context(cameras)

        self.assertFalse(side["door_articulation_candidate"])
        self.assertTrue(side["single_camera_close_activity"])
        self.assertFalse(side["hard_contact_candidate"])
        self.assertEqual("MEDIUM", side["contact_level"])

    def test_asynchronous_candidates_across_many_cameras_are_background_activity(self) -> None:
        cameras: dict[str, dict] = {}
        for camera, candidate_time in (
            ("front", 10.0),
            ("back", 20.0),
            ("left_repeater", 30.0),
            ("right_repeater", 40.0),
        ):
            evidence = _side_door_evidence()
            evidence["object_contact_time_sec"] = candidate_time
            evidence["motion_samples"] = [_motion_sample(candidate_time, 0.39, 0.21)]
            cameras[camera] = evidence

        _apply_group_contact_context(cameras)

        for evidence in cameras.values():
            self.assertTrue(evidence["distributed_uncorroborated_activity"])
            self.assertFalse(evidence["possible_contact"])
            self.assertFalse(evidence["possible_impact"])
            self.assertEqual("NONE", evidence["contact_level"])
            self.assertEqual("NONE", evidence["impact_level"])

    def test_rear_candidate_requires_rear_camera(self) -> None:
        motion = {
            "max_motion_score": 0.30,
            "localized_motion_score": 0.70,
            "motion_spike_ratio": 4.2,
            "camera_shake_score": 0.14,
        }

        side = _impact_candidate_details(copy.deepcopy(motion), True, True, "MEDIUM", "right_repeater")
        rear = _impact_candidate_details(copy.deepcopy(motion), True, True, "MEDIUM", "back")

        self.assertTrue(side["hard_contact_candidate"])
        self.assertFalse(side["rear_impact_candidate"])
        self.assertTrue(rear["hard_contact_candidate"])
        self.assertTrue(rear["rear_impact_candidate"])

    def test_weak_background_motion_on_another_camera_does_not_corroborate(self) -> None:
        # A person walking around a parked car, wind, or road vibration can
        # easily nudge a neighboring camera's motion/shake scores above zero
        # without that camera seeing anything resembling contact. Only a
        # genuinely elevated reading on the other camera (see the sibling
        # test_synchronized_second_camera_preserves_hard_contact, which uses
        # 0.6) should count as corroboration.
        side = _side_door_evidence()
        cameras = {
            "back": {"motion_samples": [_motion_sample(10.95, 0.23)]},
            "right_repeater": side,
        }

        _apply_group_contact_context(cameras)

        self.assertFalse(side["multi_camera_impact_corroborated"])
        self.assertEqual([], side["multi_camera_impact_support_cameras"])
        self.assertFalse(side["hard_contact_candidate"])

    def test_uncorroborated_close_activity_with_high_generic_motion_is_not_hard_contact(self) -> None:
        # A passerby moving briskly in front of an otherwise-static camera can
        # push camera_shake_score/motion_spike_ratio into "HIGH" territory on
        # their own -- these are generic frame statistics with no notion of
        # the ego vehicle, so "independently high" must not let an
        # uncorroborated single camera bypass the downgrade below it.
        side = _side_door_evidence()
        side["camera_shake_score"] = 0.6
        cameras = {
            "front": {"motion_samples": [_motion_sample(10.971, 0.011)]},
            "back": {"motion_samples": [_motion_sample(10.720, 0.007)]},
            "left_repeater": {"motion_samples": [_motion_sample(10.969, 0.010)]},
            "right_repeater": side,
        }

        _apply_group_contact_context(cameras)

        self.assertTrue(side["single_camera_close_activity"])
        self.assertFalse(side["multi_camera_impact_corroborated"])
        self.assertFalse(side["hard_contact_candidate"])
        self.assertFalse(side["rear_impact_candidate"])
        self.assertFalse(side["strong_impact_like_motion"])
        self.assertEqual("MEDIUM", side["impact_level"])
        self.assertEqual("MEDIUM", side["contact_level"])
        self.assertIn("image-space overlap is not physical-contact proof", side["contact_semantics_reasons"])

    def test_motion_only_hard_contact_without_bounding_box_overlap_is_also_downgraded(self) -> None:
        # This is the gap the two prior fixes in this file didn't cover:
        # hard_contact_candidate reached purely through generic
        # vehicle_detected + motion thresholds, with no object_contact_candidate
        # at all, used to skip the single-camera corroboration check entirely
        # because that check only looked at object_contact_candidate.
        motion_only = _motion_only_hard_contact_evidence()
        cameras = {
            "front": {"motion_samples": [_motion_sample(11.9, 0.01)]},
            "back": {"motion_samples": [_motion_sample(11.7, 0.01)]},
            "right_repeater": motion_only,
        }

        _apply_group_contact_context(cameras)

        self.assertTrue(motion_only["single_camera_close_activity"])
        self.assertFalse(motion_only["multi_camera_impact_corroborated"])
        self.assertFalse(motion_only["hard_contact_candidate"])
        self.assertFalse(motion_only["strong_impact_like_motion"])
        self.assertEqual("MEDIUM", motion_only["impact_level"])
        self.assertEqual("MEDIUM", motion_only["contact_level"])

    def test_motion_only_hard_contact_with_real_corroboration_is_preserved(self) -> None:
        motion_only = _motion_only_hard_contact_evidence()
        cameras = {
            "back": {"motion_samples": [_motion_sample(12.1, 0.6, 0.6)]},
            "right_repeater": motion_only,
        }

        _apply_group_contact_context(cameras)

        self.assertTrue(motion_only["multi_camera_impact_corroborated"])
        self.assertTrue(motion_only["hard_contact_candidate"])

    def test_single_camera_hard_contact_is_unchanged(self) -> None:
        evidence = _side_door_evidence()
        cameras = {"unknown": evidence}

        _apply_group_contact_context(cameras)

        self.assertFalse(evidence["door_articulation_candidate"])
        self.assertTrue(evidence["hard_contact_candidate"])
        self.assertEqual("HIGH", evidence["contact_level"])


if __name__ == "__main__":
    unittest.main()
