"""The scalars that gate severity must not depend on the scan mode.

Four separate bugs in this codebase came from the same root cause: a quantity
derived from *how many frames we happened to sample* rather than from elapsed
time. Each was found only after a user noticed the slower, supposedly more
careful mode behaving worse than the fast one.

This test covers the class rather than the instances. It feeds one synthetic
motion signal, sampled at 1/2/4/8 fps, through the same aggregation
`analyze_motion` performs, and asserts the reported scalars agree. A future
aggregate computed over the dense sample list instead of the fixed grid will
fail here.
"""

from __future__ import annotations

import unittest

from .evidence_extractor import _spike_ratio
from .motion_analysis import _selection_grid


def safe_float(value: object, default: float) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def motion_at(time_sec: float) -> float:
    """A calm clip with one sharp 0.4s impact at t=16.0."""

    if 16.0 <= time_sec <= 16.4:
        return 0.90
    if 8.0 <= time_sec <= 12.0:
        return 0.20  # a passing car: real, but not an impact
    return 0.05


def samples_at(fps: float, duration_sec: float = 29.0) -> list[dict]:
    step = 1.0 / fps
    out: list[dict] = []
    t = 0.0
    while t <= duration_sec + 1e-9:
        score = motion_at(t)
        out.append(
            {
                "time_sec": round(t, 6),
                "motion_score": score,
                "localized_motion_score": score,
                "ego_zone_motion_score": score * 0.5,
                "camera_shake_score": score * 0.25,
                "duration_sec": duration_sec,
            }
        )
        t += step
    return out


def aggregate(fps: float) -> dict[str, float]:
    """Mirror of the aggregation analyze_motion reports out."""

    grid = _selection_grid(samples_at(fps), safe_float)
    scores = [safe_float(s.get("motion_score"), 0.0) for s in grid]
    localized = [safe_float(s.get("localized_motion_score"), 0.0) for s in grid]
    shake = [safe_float(s.get("camera_shake_score"), 0.0) for s in grid]
    ego = [safe_float(s.get("ego_zone_motion_score"), 0.0) for s in grid]
    max_score = max(scores)
    return {
        "max_motion": max_score,
        "max_localized": max(localized),
        "max_camera_shake": max(shake),
        "max_ego_motion": max(ego),
        "spike_ratio": _spike_ratio(scores, max_score),
        "average": sum(scores) / len(scores),
    }


class ModeInvarianceTests(unittest.TestCase):
    RATES = (1.0, 2.0, 4.0, 8.0)

    def test_severity_gating_scalars_are_identical_across_rates(self) -> None:
        baseline = aggregate(1.0)
        for fps in self.RATES:
            got = aggregate(fps)
            for key, expected in baseline.items():
                self.assertAlmostEqual(
                    got[key],
                    expected,
                    places=6,
                    msg=f"{key} differs at {fps} fps: {got[key]} vs {expected} at 1 fps",
                )

    def test_spike_ratio_does_not_decay_with_sampling_rate(self) -> None:
        # The previous fixed-count baseline exclusion depressed this ratio at
        # higher rates, making impacts *less* likely to escalate in slow modes.
        ratios = [aggregate(fps)["spike_ratio"] for fps in self.RATES]
        self.assertTrue(
            all(r == ratios[0] for r in ratios),
            f"spike_ratio drifted across sampling rates: {list(zip(self.RATES, ratios))}",
        )

    def test_spike_ratio_still_separates_impact_from_background(self) -> None:
        # Guard against "invariant but useless": the impact must stand out.
        self.assertGreater(aggregate(1.0)["spike_ratio"], 3.0)

    def test_proportional_baseline_is_stable_as_samples_grow(self) -> None:
        # Same signal, more samples: the ratio must not move.
        few = _spike_ratio([0.05] * 9 + [0.9], 0.9)
        many = _spike_ratio([0.05] * 90 + [0.9] * 10, 0.9)
        self.assertAlmostEqual(few, many, delta=0.35)


if __name__ == "__main__":
    unittest.main()
