"""Dwell classification must not depend on the scan mode's sampling rate.

Regression guard for a real bug: the passby/lingering thresholds once tested
raw sampled-frame counts, which scale with sample_fps. A brief walk-past was
correctly dismissed at 1 fps but reported as "lingering" at 4 fps, so the
thorough scan mode produced more spurious flags than the fast one.
"""

from __future__ import annotations

import unittest

from .evidence_extractor import (
    PERSON_LINGER_MIN_SEC,
    PERSON_PASSBY_MAX_SEC,
    _dwell_flags,
)


def track(dwell_sec: float, frame_count: int, class_name: str = "person") -> dict:
    return {
        "class_name": class_name,
        "dwell_time_sec": dwell_sec,
        "frame_count": frame_count,
    }


class DwellFlagSamplingRateTests(unittest.TestCase):
    def test_brief_passby_is_not_lingering_at_any_sampling_rate(self) -> None:
        # Same 1.5s walk-past, observed at 1, 2 and 4 fps.
        for fps in (1, 2, 4):
            frames = max(1, int(1.5 * fps))
            passby, lingering = _dwell_flags(
                [track(1.5, frames)], "person", PERSON_PASSBY_MAX_SEC, PERSON_LINGER_MIN_SEC
            )
            self.assertTrue(passby, f"1.5s track should be a passby at {fps} fps")
            self.assertFalse(lingering, f"1.5s track must not be lingering at {fps} fps")

    def test_real_lingering_is_detected_at_any_sampling_rate(self) -> None:
        for fps in (1, 2, 4):
            frames = max(1, int(8.0 * fps))
            _, lingering = _dwell_flags(
                [track(8.0, frames)], "person", PERSON_PASSBY_MAX_SEC, PERSON_LINGER_MIN_SEC
            )
            self.assertTrue(lingering, f"8s track should be lingering at {fps} fps")

    def test_flags_are_identical_across_sampling_rates(self) -> None:
        # Whatever the verdict is, denser sampling must not change it.
        for dwell in (0.0, 0.5, 1.9, 2.0, 3.0, 4.0, 6.0):
            verdicts = {
                _dwell_flags(
                    [track(dwell, max(1, int(dwell * fps)))],
                    "person",
                    PERSON_PASSBY_MAX_SEC,
                    PERSON_LINGER_MIN_SEC,
                )
                for fps in (1, 2, 4, 8)
            }
            self.assertEqual(len(verdicts), 1, f"dwell={dwell}s gave rate-dependent verdicts: {verdicts}")

    def test_only_matching_class_is_considered(self) -> None:
        tracks = [track(9.0, 40, class_name="vehicle")]
        _, lingering = _dwell_flags(tracks, "person", PERSON_PASSBY_MAX_SEC, PERSON_LINGER_MIN_SEC)
        self.assertFalse(lingering, "a lingering vehicle must not set the person flag")


if __name__ == "__main__":
    unittest.main()
