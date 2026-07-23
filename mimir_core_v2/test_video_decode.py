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


if __name__ == "__main__":
    unittest.main()
