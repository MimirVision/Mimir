"""The refiner decides the impact timestamp Mimir reports, and had no tests.

Three things here have already gone wrong once and are cheap to pin:

* Signals were differenced against the immediate predecessor with divisors
  calibrated for a ~1.0s interval, so the same event produced a ~3x smaller
  signal at `thorough` than at `fast`. `signal_time_scale` normalises that.
* `_select_impulse` windowed its baseline by array index (`[-4:]`), which is
  0.67s of history at `fast` but 0.22s at `thorough` -- a different real-world
  baseline per mode.
* The fix for the first item shipped as a complete no-op, because
  `REFINEMENT_VERSION` feeds the refinement cache key and had not been bumped.
  A cached result for the old code was returned unchanged.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from . import key_moment_refiner as refiner
from .key_moment_refiner import (
    MOTION_REFERENCE_INTERVAL_SEC,
    MOTION_SCALE_MAX,
    REFINEMENT_VERSION,
    _refinement_cache_key,
    _select_impulse,
    signal_time_scale,
)

CAP_PROP_FRAME_COUNT = 7
CAP_PROP_FPS = 5


class SignalTimeScaleTest(unittest.TestCase):
    def test_reference_interval_is_unchanged(self) -> None:
        self.assertAlmostEqual(signal_time_scale(MOTION_REFERENCE_INTERVAL_SEC), 1.0)

    def test_shorter_intervals_are_scaled_up_and_longer_ones_down(self) -> None:
        # An absdiff over half the interval carries half the motion, so it must
        # be doubled to compare against the reference.
        self.assertAlmostEqual(signal_time_scale(MOTION_REFERENCE_INTERVAL_SEC / 2), 2.0)
        self.assertAlmostEqual(signal_time_scale(MOTION_REFERENCE_INTERVAL_SEC * 2), 0.5)

    def test_scale_is_capped_so_a_tiny_gap_cannot_explode_the_signal(self) -> None:
        self.assertEqual(signal_time_scale(1e-9), MOTION_SCALE_MAX)

    def test_non_positive_delta_is_neutral(self) -> None:
        self.assertEqual(signal_time_scale(0.0), 1.0)
        self.assertEqual(signal_time_scale(-1.0), 1.0)

    def test_normalisation_makes_the_same_event_equal_across_sampling_rates(self) -> None:
        # A scene moving at a constant rate produces a diff proportional to the
        # gap. After scaling, every sampling rate must report the same signal.
        raw_per_second = 0.6
        scaled = [
            raw_per_second * delta * signal_time_scale(delta)
            for delta in (1 / 6.0, 1 / 12.0, 1 / 18.0)
        ]
        for value in scaled[1:]:
            self.assertAlmostEqual(value, scaled[0], places=6)


class RefinementCacheKeyTest(unittest.TestCase):
    """A stale cache silently reverts any change to how refinement works."""

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.video = Path(self._temp.name) / "clip.mp4"
        self.video.write_bytes(b"pretend footage")

    def tearDown(self) -> None:
        self._temp.cleanup()

    def _key(self, **overrides: object) -> str:
        arguments: dict = {
            "source_video": str(self.video),
            "camera": "front",
            "coarse_time": 16.2,
            "coarse_source": "motion",
            "mode": "thorough",
            "contact_expected": True,
            "articulated_contact_expected": False,
            "calibration_path": None,
        }
        arguments.update(overrides)
        return _refinement_cache_key(**arguments)  # type: ignore[arg-type]

    def test_key_is_stable_for_identical_inputs(self) -> None:
        self.assertEqual(self._key(), self._key())
        self.assertTrue(self._key())

    def test_version_string_participates_in_the_key(self) -> None:
        # This is the property that was violated: the refinement logic changed
        # but REFINEMENT_VERSION did not, so cached results kept coming back
        # and the fix produced byte-identical output.
        baseline = self._key()
        original = refiner.REFINEMENT_VERSION
        try:
            refiner.REFINEMENT_VERSION = f"{original}_changed"
            self.assertNotEqual(
                baseline,
                self._key(),
                "changing REFINEMENT_VERSION must invalidate cached refinements",
            )
        finally:
            refiner.REFINEMENT_VERSION = original

    def test_version_is_recorded_and_non_empty(self) -> None:
        self.assertTrue(REFINEMENT_VERSION)

    def test_inputs_that_change_the_answer_change_the_key(self) -> None:
        baseline = self._key()
        for field, value in (
            ("camera", "left_repeater"),
            ("coarse_time", 12.4),
            ("mode", "fast"),
            ("contact_expected", False),
            ("coarse_source", "detector"),
        ):
            with self.subTest(field=field):
                self.assertNotEqual(baseline, self._key(**{field: value}))

    def test_missing_source_yields_no_key_so_nothing_is_cached(self) -> None:
        self.assertEqual(self._key(source_video=str(self.video.parent / "gone.mp4")), "")


def _signal(time_sec: float, value: float, **extra: float) -> dict:
    item = {"time_sec": time_sec, "signal": value}
    item.update(extra)
    return item


class SelectImpulseTest(unittest.TestCase):
    def test_empty_input_is_handled(self) -> None:
        self.assertEqual(_select_impulse([], coarse_time=10.0), {})

    def test_picks_the_spike_over_a_quiet_run(self) -> None:
        signals = [_signal(index * 0.1, 0.05) for index in range(20)]
        signals[12]["signal"] = 0.9
        best = _select_impulse(signals, coarse_time=1.2)

        self.assertAlmostEqual(best["time_sec"], 1.2, places=3)
        self.assertGreater(best["impulse"], 0.0)
        self.assertGreater(best["confidence"], 0.0)

    def test_baseline_history_is_measured_in_seconds_not_samples(self) -> None:
        # The same event sampled at two rates must produce the same impulse.
        # Index-based lookbacks made the denser sampling compare against a much
        # shorter stretch of history, inflating its impulse.
        def run(interval: float) -> float:
            count = int(round(2.0 / interval))
            signals = [_signal(index * interval, 0.10) for index in range(count)]
            signals[-1]["signal"] = 0.70
            return _select_impulse(signals, coarse_time=signals[-1]["time_sec"])["impulse"]

        sparse = run(1 / 6.0)
        dense = run(1 / 18.0)
        self.assertAlmostEqual(sparse, dense, places=5)

    def test_proximity_breaks_ties_toward_the_coarse_estimate(self) -> None:
        signals = [_signal(index * 0.5, 0.1) for index in range(20)]
        signals[4]["signal"] = 0.6
        signals[16]["signal"] = 0.6

        near_first = _select_impulse(signals, coarse_time=2.0)
        near_second = _select_impulse(signals, coarse_time=8.0)

        self.assertAlmostEqual(near_first["time_sec"], 2.0, places=3)
        self.assertAlmostEqual(near_second["time_sec"], 8.0, places=3)

    def test_contact_mode_never_selects_earlier_than_plain_mode(self) -> None:
        # Plain mode is drawn to the motion onset. Contact mode weights a
        # mature contact breaking down, so it can only move the answer later
        # into (or past) the contact -- never back before it started.
        signals = []
        for index in range(24):
            time_sec = index * 0.1
            contact = 0.9 if 8 <= index <= 17 else 0.0
            value = 0.5 if 8 <= index <= 17 else 0.1
            signals.append(_signal(time_sec, value, apparent_contact_score=contact))
        # Contact ends: score collapses and the camera shakes.
        signals[18]["camera_shift"] = 0.2

        plain = _select_impulse(signals, coarse_time=1.3, contact_expected=False)
        contact = _select_impulse(signals, coarse_time=1.3, contact_expected=True)

        self.assertGreaterEqual(contact["time_sec"], plain["time_sec"])
        self.assertGreaterEqual(contact["time_sec"], 0.8, "must land at or after contact onset")

    def test_terminal_contact_is_scored_only_once_contact_has_matured(self) -> None:
        # The mechanism that moves the answer to the end of a contact: a drop
        # after a sustained contact scores, an identical drop after a brief
        # one does not.
        def run(contact_frames: int) -> float:
            signals = []
            for index in range(contact_frames + 4):
                contact = 0.9 if index < contact_frames else 0.0
                value = 0.5 if index < contact_frames else 0.1
                signals.append(_signal(index * 0.1, value, apparent_contact_score=contact))
            scored = _select_impulse(signals, coarse_time=contact_frames * 0.1, contact_expected=True)
            return scored["terminal_contact_score"]

        # mature_contact ramps over 0.6s, so 2 frames (0.2s) is immature.
        self.assertEqual(run(2), 0.0)
        self.assertGreater(run(12), 0.0)

    def test_flat_signal_still_returns_a_candidate_without_dividing_by_zero(self) -> None:
        signals = [_signal(index * 0.1, 0.2) for index in range(10)]
        best = _select_impulse(signals, coarse_time=0.5)

        self.assertIn("time_sec", best)
        self.assertEqual(best["impulse"], 0.0)
        self.assertGreaterEqual(best["confidence"], 0.0)


class StubCapture:
    def __init__(self, frame_count: int, fps: float) -> None:
        self._values = {CAP_PROP_FRAME_COUNT: frame_count, CAP_PROP_FPS: fps}
        self.released = False

    def isOpened(self) -> bool:  # noqa: N802 - mirrors the OpenCV API
        return True

    def get(self, prop: int) -> float:
        return float(self._values.get(prop, 0.0))

    def release(self) -> None:
        self.released = True


class StubFrame:
    shape = (720, 1280, 3)


class StubCv2:
    CAP_PROP_FRAME_COUNT = CAP_PROP_FRAME_COUNT
    CAP_PROP_FPS = CAP_PROP_FPS

    def __init__(self, frame_count: int, fps: float) -> None:
        self.frame_count = frame_count
        self.fps = fps

    def VideoCapture(self, _path: str) -> StubCapture:  # noqa: N802 - mirrors OpenCV
        return StubCapture(self.frame_count, self.fps)

    def resize(self, _frame, _size):
        return StubFrame()


class ReadFramesDecimationTest(unittest.TestCase):
    """Uneven gaps become motion spikes, because an absdiff scales with the gap."""

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.video = Path(self._temp.name) / "clip.mp4"
        self.video.write_bytes(b"pretend footage")

        self._real_load_cv2 = refiner._load_cv2
        self._real_read = refiner.read_frames_at_indexes
        self.requested: list[int] = []

        def fake_read(_capture, indexes, _cv2):
            self.requested = sorted({int(index) for index in indexes})
            return [(index, StubFrame()) for index in self.requested]

        refiner.read_frames_at_indexes = fake_read
        refiner._load_cv2 = lambda: StubCv2(int(60 * 36), 36.0)

    def tearDown(self) -> None:
        refiner._load_cv2 = self._real_load_cv2
        refiner.read_frames_at_indexes = self._real_read
        self._temp.cleanup()

    def test_capped_window_is_sampled_without_double_length_gaps(self) -> None:
        # A wide window with a tight frame budget forces the cap to bind.
        settings = {"window": 30.0, "fps": 24.0, "max_frames": 40}
        frames, metrics = refiner._read_frames(self.video, center_sec=30.0, settings=settings)

        self.assertEqual(len(frames), len(self.requested))
        self.assertLessEqual(len(frames), 40)
        gaps = [b - a for a, b in zip(self.requested, self.requested[1:])]
        self.assertLessEqual(
            max(gaps),
            min(gaps) + 1,
            f"uneven refinement grid: gaps ranged {min(gaps)}-{max(gaps)}",
        )
        self.assertGreater(metrics["native_fps"], 0)

    def test_uncapped_window_keeps_the_requested_step(self) -> None:
        settings = {"window": 4.0, "fps": 12.0, "max_frames": 54}
        refiner._read_frames(self.video, center_sec=20.0, settings=settings)

        gaps = {b - a for a, b in zip(self.requested, self.requested[1:])}
        self.assertEqual(gaps, {3}, "36 fps sampled at 12 fps should step by 3")

    def test_window_is_clamped_to_the_clip(self) -> None:
        settings = {"window": 20.0, "fps": 12.0, "max_frames": 54}
        _, metrics = refiner._read_frames(self.video, center_sec=1.0, settings=settings)

        self.assertGreaterEqual(metrics["start_sec"], 0.0)
        self.assertLessEqual(metrics["end_sec"], metrics["duration_sec"])

    def test_missing_file_returns_empty_without_raising(self) -> None:
        frames, metrics = refiner._read_frames(
            self.video.parent / "gone.mp4", center_sec=10.0, settings={"window": 4.0}
        )
        self.assertEqual(frames, [])
        self.assertEqual(metrics, {})

    def test_unreadable_video_metadata_returns_empty(self) -> None:
        refiner._load_cv2 = lambda: StubCv2(0, 0.0)
        frames, metrics = refiner._read_frames(
            self.video, center_sec=10.0, settings={"window": 4.0}
        )
        self.assertEqual(frames, [])
        self.assertEqual(metrics, {})


if __name__ == "__main__":
    unittest.main()
