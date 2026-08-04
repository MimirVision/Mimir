"""Sampling must actually deliver the rate each mode advertises.

The `max_frames` caps are the whole story here. They were sized for a 60s clip;
on anything longer the cap binds and the advertised rate silently drops, so
`thorough` samples at `balanced`'s rate and brief contact events (0.2-0.5s) can
fall entirely between two sampled frames. `clip_metrics` used to report the
*requested* rate, which meant diagnostics agreed with the advertised number
rather than with what happened.

These tests run the sampler against a stub OpenCV so the index arithmetic can be
pinned without a real video file.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from . import frame_sampler

CAP_PROP_FRAME_COUNT = 7
CAP_PROP_FPS = 5


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


class StubCv2:
    CAP_PROP_FRAME_COUNT = CAP_PROP_FRAME_COUNT
    CAP_PROP_FPS = CAP_PROP_FPS

    def __init__(self, frame_count: int, fps: float) -> None:
        self.frame_count = frame_count
        self.fps = fps
        self.captures: list[StubCapture] = []

    def VideoCapture(self, _path: str) -> StubCapture:  # noqa: N802 - mirrors OpenCV
        capture = StubCapture(self.frame_count, self.fps)
        self.captures.append(capture)
        return capture


class StubFrame:
    shape = (720, 1280, 3)


class FrameSamplerTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.video = Path(self._temp.name) / "clip.mp4"
        self.video.write_bytes(b"not a real video")

        self._real_load_cv2 = frame_sampler._load_cv2
        self._real_read = frame_sampler.read_frames_at_indexes
        self.requested_indexes: list[int] = []

        def fake_read(_capture, indexes, _cv2):
            ordered = sorted({max(0, int(index)) for index in indexes})
            self.requested_indexes = ordered
            return [(index, StubFrame()) for index in ordered]

        frame_sampler.read_frames_at_indexes = fake_read

    def tearDown(self) -> None:
        frame_sampler._load_cv2 = self._real_load_cv2
        frame_sampler.read_frames_at_indexes = self._real_read
        self._temp.cleanup()

    def _sample(self, mode: str, duration_sec: float, fps: float = 36.0):
        stub = StubCv2(int(round(duration_sec * fps)), fps)
        frame_sampler._load_cv2 = lambda: stub
        group = {"clips": [{"camera": "front", "path": str(self.video)}]}
        result, warnings = frame_sampler.sample_event_group(group, mode=mode)
        return result, warnings, stub

    def test_each_mode_delivers_its_advertised_rate_on_a_sentry_length_clip(self) -> None:
        # Tesla Sentry clips are ~60s; this is the length the caps were sized for.
        for mode, expected_fps in (("fast", 1.0), ("balanced", 2.0), ("thorough", 4.0)):
            with self.subTest(mode=mode):
                result, _, _ = self._sample(mode, duration_sec=60.0)
                metrics = result["clip_metrics"][0]
                self.assertAlmostEqual(metrics["sample_fps"], expected_fps, delta=0.1)
                self.assertEqual(metrics["requested_sample_fps"], expected_fps)

    def test_modes_stay_ordered_when_the_cap_binds_on_a_long_clip(self) -> None:
        # Past 60s every mode is capped. The rates converge, but thorough must
        # never fall to or below balanced -- that was the silent downgrade.
        rates = {}
        for mode in ("fast", "balanced", "thorough"):
            result, _, _ = self._sample(mode, duration_sec=180.0)
            rates[mode] = result["clip_metrics"][0]["sample_fps"]

        self.assertGreater(rates["thorough"], rates["balanced"])
        self.assertGreater(rates["balanced"], rates["fast"])

    def test_reported_rate_is_the_achieved_one_not_the_requested_one(self) -> None:
        result, _, _ = self._sample("thorough", duration_sec=180.0)
        metrics = result["clip_metrics"][0]

        self.assertEqual(metrics["requested_sample_fps"], 4.0)
        self.assertLess(
            metrics["sample_fps"],
            metrics["requested_sample_fps"],
            "a capped clip must not claim it hit the requested rate",
        )
        self.assertAlmostEqual(
            metrics["sample_fps"],
            metrics["sampled_frames"] / metrics["duration_sec"],
            places=2,
        )

    def test_decimation_leaves_no_double_length_gap(self) -> None:
        # Motion is a frame difference, so a gap of 2x the nominal interval
        # reads as a spurious spike. Truncating decimation used to produce
        # scattered double gaps.
        self._sample("thorough", duration_sec=300.0)
        indexes = self.requested_indexes
        self.assertGreater(len(indexes), 2)

        gaps = [b - a for a, b in zip(indexes, indexes[1:])]
        self.assertLessEqual(
            max(gaps),
            min(gaps) + 1,
            f"uneven sampling grid: gaps ranged {min(gaps)}-{max(gaps)}",
        )

    def test_frame_count_never_exceeds_the_mode_cap(self) -> None:
        for mode, cap in (("fast", 60), ("balanced", 120), ("thorough", 240)):
            with self.subTest(mode=mode):
                result, _, _ = self._sample(mode, duration_sec=600.0)
                self.assertLessEqual(result["sampled_frames"], cap)

    def test_short_clip_samples_below_the_cap_without_padding(self) -> None:
        result, _, _ = self._sample("thorough", duration_sec=5.0)
        # 5s at 4 fps is ~20 frames, nowhere near the 240 cap.
        self.assertLess(result["sampled_frames"], 30)
        self.assertGreater(result["sampled_frames"], 10)

    def test_missing_video_warns_and_still_reports_metrics(self) -> None:
        stub = StubCv2(2160, 36.0)
        frame_sampler._load_cv2 = lambda: stub
        group = {"clips": [{"camera": "front", "path": str(self.video.parent / "gone.mp4")}]}
        result, warnings = frame_sampler.sample_event_group(group, mode="balanced")

        self.assertEqual(result["sampled_frames"], 0)
        self.assertEqual(len(result["clip_metrics"]), 1)
        self.assertTrue(any("missing" in warning.lower() for warning in warnings))

    def test_capture_is_released_even_though_sampling_succeeded(self) -> None:
        _, _, stub = self._sample("fast", duration_sec=60.0)
        self.assertTrue(all(capture.released for capture in stub.captures))

    def test_unknown_mode_falls_back_to_balanced(self) -> None:
        result, _, _ = self._sample("turbo", duration_sec=60.0)
        self.assertAlmostEqual(result["clip_metrics"][0]["requested_sample_fps"], 2.0)

    def test_zero_fps_video_does_not_divide_by_zero(self) -> None:
        stub = StubCv2(100, 0.0)
        frame_sampler._load_cv2 = lambda: stub
        group = {"clips": [{"camera": "front", "path": str(self.video)}]}
        result, _ = frame_sampler.sample_event_group(group, mode="balanced")

        metrics = result["clip_metrics"][0]
        self.assertEqual(metrics["duration_sec"], 0.0)
        self.assertEqual(metrics["sample_fps"], 2.0)

    def test_missing_opencv_returns_empty_result_with_a_warning(self) -> None:
        frame_sampler._load_cv2 = lambda: None
        group = {"clips": [{"camera": "front", "path": str(self.video)}]}
        result, warnings = frame_sampler.sample_event_group(group, mode="balanced")

        self.assertEqual(result["sampled_frames"], 0)
        self.assertTrue(any("OpenCV" in warning for warning in warnings))


if __name__ == "__main__":
    unittest.main()
