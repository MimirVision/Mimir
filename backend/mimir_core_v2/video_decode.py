"""Exact-index video reads without a costly seek before every frame."""

from __future__ import annotations

from typing import Any, Iterable


def read_frames_at_indexes(capture: Any, indexes: Iterable[int], cv2: Any) -> list[tuple[int, Any]]:
    """Read sorted frame indexes with one initial seek and sequential grabs.

    A direct seek is retained as a recovery path for malformed videos. The
    returned indexes always identify the requested source frames.
    """

    requested = sorted({max(0, int(index)) for index in indexes})
    if not requested:
        return []

    first = requested[0]
    capture.set(cv2.CAP_PROP_POS_FRAMES, first)
    position = first
    frames: list[tuple[int, Any]] = []

    for target in requested:
        if target < position:
            capture.set(cv2.CAP_PROP_POS_FRAMES, target)
            position = target

        sequential_ok = True
        while position < target:
            if not capture.grab():
                sequential_ok = False
                break
            position += 1

        if not sequential_ok:
            capture.set(cv2.CAP_PROP_POS_FRAMES, target)
            position = target

        ok, frame = capture.read()
        position = target + 1
        if not ok or frame is None:
            capture.set(cv2.CAP_PROP_POS_FRAMES, target)
            ok, frame = capture.read()
            position = target + 1
        if ok and frame is not None:
            frames.append((target, frame))

    return frames
