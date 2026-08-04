"""Candidate selection must happen on a fixed grid, not on every sampled frame.

Which event becomes the incident is decided by candidate selection, and the
1 fps `fast` mode was the one getting that right. Denser modes selected on every
sampled frame, surfacing extra short-lived clusters that 1 fps never saw, and
drifted onto different events (thorough reported an impact 10s late).

Selecting on a fixed ~1.0s grid makes every mode pick the same event as fast by
construction. These tests pin that grid.
"""

from __future__ import annotations

import unittest

from .motion_analysis import MOTION_BASELINE_SEC, _selection_grid


def safe_float(value: object, default: float) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def samples_at(fps: float, duration_sec: float) -> list[dict]:
    step = 1.0 / fps
    out: list[dict] = []
    t = 0.0
    while t <= duration_sec + 1e-9:
        out.append({"time_sec": round(t, 6)})
        t += step
    return out


class SelectionGridTests(unittest.TestCase):
    def test_one_fps_input_is_unchanged(self) -> None:
        # fast must be byte-identical: its samples already sit on the grid.
        samples = samples_at(1.0, 20.0)
        self.assertEqual(_selection_grid(samples, safe_float), samples)

    def test_denser_modes_collapse_onto_the_same_grid(self) -> None:
        expected = [s["time_sec"] for s in _selection_grid(samples_at(1.0, 20.0), safe_float)]
        for fps in (2.0, 4.0, 8.0):
            got = [s["time_sec"] for s in _selection_grid(samples_at(fps, 20.0), safe_float)]
            self.assertEqual(got, expected, f"{fps} fps produced a different selection grid")

    def test_grid_spacing_is_at_least_one_baseline(self) -> None:
        for fps in (1.0, 2.0, 4.0, 8.0):
            times = [s["time_sec"] for s in _selection_grid(samples_at(fps, 30.0), safe_float)]
            gaps = [b - a for a, b in zip(times, times[1:])]
            self.assertTrue(
                all(gap >= MOTION_BASELINE_SEC - 1e-6 for gap in gaps),
                f"{fps} fps grid had gaps below the baseline: {gaps[:5]}",
            )

    def test_irregular_timestamps_still_respect_spacing(self) -> None:
        # Dropped/corrupt frames mean timestamps are not always evenly spaced.
        samples = [{"time_sec": t} for t in (0.0, 0.1, 0.2, 1.05, 1.1, 3.7, 3.8, 4.9)]
        times = [s["time_sec"] for s in _selection_grid(samples, safe_float)]
        self.assertEqual(times, [0.0, 1.05, 3.7, 4.9])

    def test_empty_input(self) -> None:
        self.assertEqual(_selection_grid([], safe_float), [])


if __name__ == "__main__":
    unittest.main()
