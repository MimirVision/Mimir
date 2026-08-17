"""A box around the whole frame is a failed detection, not a close vehicle.

Measured over 619 close-vehicle detections from four real event groups, the
areas are bimodal rather than continuous:

    0.00-0.20  259
    0.20-0.40   82
    0.40-0.60  146
    0.60-0.80   25
    0.80-0.90    3
    0.90-0.95    0      <- nothing here
    0.95-1.01  104      <- 17%, almost all on one camera

Real vehicles thin out above 0.8. The cluster at ~0.99 is the detector
returning a box around the entire image on low-contrast night footage. Each of
those satisfies `area_ratio >= 0.12`, which marks a close vehicle, which sets
contact_level MEDIUM, which sets possible_contact, which blocks the
normal_traffic rule meant to say "this is just a car park" -- so the event
landed in REVIEW, on nearly every event.

This started as a different fix. The theory was that the car detects its own
bodywork, and the numbers said otherwise: only 12% of these boxes sit mostly
inside the ego region, because a full-frame box overlaps that region by exactly
the fraction of the frame it occupies. Probing the real detector rather than
trusting the stored max_area/max_bottom summary is what caught it.

The dangerous direction is the other one, so most of these tests are about the
rule NOT firing.
"""

from __future__ import annotations

import unittest

from mimir_core_v2.evidence_extractor import (
    _close_object_evidence,
    _is_frame_filling_detection,
)

FRAME_W = 1280.0
FRAME_H = 960.0


def detection(class_name: str, x1: float, y1: float, x2: float, y2: float) -> dict:
    """Bbox given in normalised coordinates, returned in pixels like the detector."""

    return {
        "class_name": class_name,
        "bbox": [x1 * FRAME_W, y1 * FRAME_H, x2 * FRAME_W, y2 * FRAME_H],
        "frame_width": FRAME_W,
        "frame_height": FRAME_H,
    }


def fills(x1: float, y1: float, x2: float, y2: float) -> bool:
    width, height = x2 - x1, y2 - y1
    return _is_frame_filling_detection(width * height, width, height)


class RejectsFailedDetectionsTest(unittest.TestCase):
    def test_the_measured_failure_cluster(self) -> None:
        # The real shape: area 0.992, bottom edge 0.995, on the back camera.
        self.assertTrue(fills(0.002, 0.002, 0.998, 0.997))

    def test_an_exactly_full_frame_box(self) -> None:
        self.assertTrue(fills(0.0, 0.0, 1.0, 1.0))

    def test_a_tall_thin_full_height_box_is_not_caught_by_the_side_rule(self) -> None:
        # Full height but a third of the width is a plausible close vehicle on
        # a repeater, and its area is nowhere near the bar.
        self.assertFalse(fills(0.0, 0.0, 0.33, 1.0))


class KeepsRealVehiclesTest(unittest.TestCase):
    def test_the_largest_plausible_detection_measured(self) -> None:
        # The real distribution tops out around 0.8 before the empty band.
        self.assertFalse(fills(0.05, 0.10, 0.95, 0.98))

    def test_a_vehicle_filling_most_of_a_repeater_view(self) -> None:
        # 0.60-0.80 band: 25 real detections live here.
        self.assertFalse(fills(0.05, 0.15, 0.95, 0.90))

    def test_a_car_pressed_against_the_camera(self) -> None:
        # The event Mimir exists to catch. Large, but not the whole frame.
        self.assertFalse(fills(0.10, 0.20, 0.90, 0.95))

    def test_an_ordinary_nearby_vehicle(self) -> None:
        self.assertFalse(fills(0.30, 0.40, 0.70, 0.75))


class CloseObjectEvidenceTest(unittest.TestCase):
    def test_a_frame_filling_box_produces_no_close_vehicle(self) -> None:
        evidence = _close_object_evidence([detection("vehicle", 0.002, 0.002, 0.998, 0.997)])

        self.assertNotIn("vehicle", evidence.get("close_classes", []))

    def test_a_real_close_vehicle_still_registers_alongside_a_failed_box(self) -> None:
        """The property that stops this hiding an actual incident."""

        evidence = _close_object_evidence(
            [
                detection("vehicle", 0.0, 0.0, 1.0, 1.0),      # failed detection
                detection("vehicle", 0.15, 0.20, 0.70, 0.62),  # a real vehicle
            ]
        )

        self.assertIn("vehicle", evidence.get("close_classes", []))

    def test_a_person_is_never_affected(self) -> None:
        # The rule is about box geometry, but a person filling the frame is the
        # same failure and should be treated the same way.
        evidence = _close_object_evidence(
            [
                detection("vehicle", 0.0, 0.0, 1.0, 1.0),
                detection("person", 0.40, 0.30, 0.62, 0.95),
            ]
        )

        self.assertIn("person", evidence.get("close_classes", []))

    def test_the_reported_area_is_the_one_that_was_decided_on(self) -> None:
        """The reason string must describe the kept box, not the rejected one.

        These strings are how the pipeline's decisions get diagnosed at all --
        the distribution in _is_frame_filling_detection was read out of them.
        An excluded box leaking its 0.99 back into the reason would make the
        evidence say the opposite of what the resolver acted on.
        """

        evidence = _close_object_evidence(
            [
                detection("vehicle", 0.0, 0.0, 1.0, 1.0),      # excluded
                detection("vehicle", 0.15, 0.20, 0.70, 0.62),  # kept: area 0.231
            ]
        )

        reasons = " ".join(evidence.get("reasons", []))
        self.assertIn("max_area=0.231", reasons)
        self.assertNotIn("max_area=1.000", reasons)
        self.assertEqual(evidence.get("frame_filling_detections"), 1)

    def test_nothing_at_all_still_returns_a_usable_shape(self) -> None:
        evidence = _close_object_evidence([])

        self.assertEqual(list(evidence.get("close_classes", [])), [])
        self.assertEqual(evidence.get("frame_filling_detections"), 0)


if __name__ == "__main__":
    unittest.main()
