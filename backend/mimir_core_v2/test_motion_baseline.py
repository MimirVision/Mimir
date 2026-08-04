"""Motion deltas must be measured over a fixed time baseline, not per sample.

An absdiff's magnitude depends on how much time separates the two frames, so
differencing against the immediately preceding *sampled* frame made a motion
score mean "change per sampling interval" -- a different quantity in each scan
mode. These tests pin the reference-selection behaviour that removes that
dependence.
"""

from __future__ import annotations

import unittest

from .motion_analysis import (
    MOTION_BASELINE_MIN_RATIO,
    MOTION_BASELINE_SEC,
    _baseline_reference,
)


def history_at(fps: float, upto_sec: float) -> list[tuple[float, str]]:
    step = 1.0 / fps
    out: list[tuple[float, str]] = []
    t = 0.0
    while t <= upto_sec + 1e-9:
        out.append((round(t, 6), f"frame@{t:.3f}"))
        t += step
    return out


class BaselineReferenceTests(unittest.TestCase):
    def test_interval_is_one_baseline_regardless_of_sampling_rate(self) -> None:
        # The measured interval is what sets a motion score's magnitude, so it
        # must be ~MOTION_BASELINE_SEC at every sampling rate.
        for fps in (1.0, 2.0, 4.0, 8.0):
            now = 6.0
            reference = _baseline_reference(history_at(fps, now - 1.0 / fps), now)
            self.assertIsNotNone(reference, f"expected a reference at {fps} fps")
            interval = now - reference[0]
            self.assertAlmostEqual(
                interval,
                MOTION_BASELINE_SEC,
                delta=1.0 / fps,
                msg=f"{fps} fps measured {interval}s instead of ~{MOTION_BASELINE_SEC}s",
            )

    def test_no_reference_before_a_full_baseline_is_available(self) -> None:
        # Early in a clip there is no frame a full baseline back; emitting a
        # short-interval delta there would not be comparable to the rest.
        history = history_at(4.0, 0.5)
        self.assertIsNone(_baseline_reference(history, 0.5))

    def test_empty_history_has_no_reference(self) -> None:
        self.assertIsNone(_baseline_reference([], 3.0))

    def test_reference_is_never_too_recent(self) -> None:
        for fps in (1.0, 2.0, 4.0, 8.0):
            now = 5.0
            reference = _baseline_reference(history_at(fps, now - 1.0 / fps), now)
            if reference is None:
                continue
            self.assertGreaterEqual(
                now - reference[0],
                MOTION_BASELINE_SEC * MOTION_BASELINE_MIN_RATIO,
                f"{fps} fps picked a reference that is too recent",
            )


if __name__ == "__main__":
    unittest.main()
