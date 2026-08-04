"""Frame-index parity checks for the sequential decoder."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from .video_decode import read_frames_at_indexes


class VideoDecodeTests(unittest.TestCase):
    def test_sequential_reads_match_direct_seek_pixels(self) -> None:
        try:
            import cv2  # type: ignore
            import numpy as np  # type: ignore
        except ImportError:
            self.skipTest("OpenCV and NumPy are required")

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "fixture.avi"
            writer = cv2.VideoWriter(
                str(path),
                cv2.VideoWriter_fourcc(*"MJPG"),
                12.0,
                (96, 64),
            )
            self.assertTrue(writer.isOpened())
            for index in range(36):
                frame = np.zeros((64, 96, 3), dtype=np.uint8)
                frame[:, :, 0] = index * 5
                frame[8:24, index : index + 20, 1] = 220
                writer.write(frame)
            writer.release()

            indexes = [0, 6, 12, 18, 24, 30, 35]
            direct = cv2.VideoCapture(str(path))
            expected: dict[int, object] = {}
            try:
                for index in indexes:
                    direct.set(cv2.CAP_PROP_POS_FRAMES, index)
                    ok, frame = direct.read()
                    self.assertTrue(ok)
                    expected[index] = frame
            finally:
                direct.release()

            sequential = cv2.VideoCapture(str(path))
            try:
                actual = dict(read_frames_at_indexes(sequential, indexes, cv2))
            finally:
                sequential.release()

            self.assertEqual(set(actual), set(expected))
            for index in indexes:
                self.assertTrue(np.array_equal(actual[index], expected[index]), index)


class _FakeCapture:
    """Minimal capture double: frame N is the integer N, grab/read never fail unless told to."""

    def __init__(self, fail_grab_at: set[int] | None = None, fail_read_at: set[int] | None = None) -> None:
        self.position = 0
        self._fail_grab_at = fail_grab_at or set()
        self._fail_read_at = fail_read_at or set()

    def set(self, _prop: object, value: int) -> None:
        self.position = int(value)

    def grab(self) -> bool:
        if self.position in self._fail_grab_at:
            return False
        self.position += 1
        return True

    def read(self) -> tuple[bool, object]:
        if self.position in self._fail_read_at:
            self._fail_read_at.discard(self.position)  # fail only the first read at this position
            return False, None
        return True, self.position


class _FakeCv2:
    CAP_PROP_POS_FRAMES = 1


class VideoDecodeFakeCaptureTests(unittest.TestCase):
    def test_empty_indexes_returns_empty_list(self) -> None:
        self.assertEqual(read_frames_at_indexes(_FakeCapture(), [], _FakeCv2()), [])

    def test_negative_indexes_clamp_to_zero(self) -> None:
        frames = read_frames_at_indexes(_FakeCapture(), [-5], _FakeCv2())
        self.assertEqual(frames, [(0, 0)])

    def test_duplicate_and_unsorted_indexes_are_deduplicated_and_ordered(self) -> None:
        frames = read_frames_at_indexes(_FakeCapture(), [5, 1, 5, 3, 1], _FakeCv2())
        self.assertEqual([index for index, _ in frames], [1, 3, 5])

    def test_grab_failure_recovers_via_direct_seek(self) -> None:
        # Reading frame 0 then sequentially grabbing towards frame 3 hits a grab
        # failure at frame 1; the function must fall back to a direct seek instead
        # of silently losing the requested frame.
        capture = _FakeCapture(fail_grab_at={1})
        frames = read_frames_at_indexes(capture, [0, 3], _FakeCv2())
        self.assertEqual(frames, [(0, 0), (3, 3)])

    def test_read_failure_retries_with_a_direct_seek(self) -> None:
        capture = _FakeCapture(fail_read_at={2})
        frames = read_frames_at_indexes(capture, [2], _FakeCv2())
        # After the first read fails, the function re-seeks and reads again; the fake
        # capture only fails the read once per position so the retry succeeds.
        self.assertEqual(frames, [(2, 2)])


if __name__ == "__main__":
    unittest.main()
