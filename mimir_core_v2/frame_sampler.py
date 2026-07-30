"""Lightweight frame sampling hooks for Core v2.

Sampling is intentionally sparse. Core v2 should inspect enough frames to build
local evidence, but it must not process every frame by default.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .video_decode import read_frames_at_indexes


def _load_cv2() -> Any:
    try:
        import cv2  # type: ignore

        return cv2
    except Exception:
        return None


def sample_event_group(event_group: dict, mode: str = "balanced") -> tuple[dict, list[str]]:
    cv2 = _load_cv2()
    warnings: list[str] = []
    # max_frames must be large enough to actually deliver sample_fps across a
    # full-length clip, otherwise the cap silently halves (or worse) the
    # requested rate. Tesla Sentry clips are ~60s, so a 60s budget is the
    # sizing target: 60s * sample_fps. Previously balanced asked for 2 fps but
    # the 60-frame cap reduced it to ~1 fps on a 60s clip, and thorough asked
    # for 4 fps and got ~2 -- brief contact events (0.2-0.5s) could fall
    # entirely between two sampled frames and never reach the detector.
    settings = {
        "fast": {"sample_fps": 1.0, "max_frames": 60},
        "balanced": {"sample_fps": 2.0, "max_frames": 120},
        "thorough": {"sample_fps": 4.0, "max_frames": 240},
    }.get(mode, {"sample_fps": 2.0, "max_frames": 120})
    sample_fps = float(settings["sample_fps"])
    max_frames = int(settings["max_frames"])
    samples: list[dict] = []

    if cv2 is None:
        warnings.append("OpenCV is not available; frame sampling metadata is limited.")
        return {"sampled_frames": 0, "samples": [], "clip_metrics": []}, warnings

    clip_metrics: list[dict] = []
    for clip in event_group.get("clips", []):
        path = Path(str(clip.get("path") or ""))
        if not path.exists():
            warnings.append(f"Video file is missing: {path}")
            clip_metrics.append({"camera": clip.get("camera", "unknown"), "sampled_frames": 0, "duration_sec": 0.0})
            continue

        capture = cv2.VideoCapture(str(path))
        try:
            if not capture.isOpened():
                warnings.append(f"Could not open video for sampling: {path}")
                clip_metrics.append({"camera": clip.get("camera", "unknown"), "sampled_frames": 0, "duration_sec": 0.0})
                continue

            frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
            duration_sec = frame_count / fps if fps > 0 else 0.0
            clip["duration_sec"] = round(duration_sec, 3)

            indexes = []
            if frame_count > 0 and fps > 0:
                step = max(1, int(round(fps / sample_fps)))
                indexes = list(range(0, frame_count, step))
                if len(indexes) > max_frames:
                    # Evenly spaced. int(index * stride) truncation dropped
                    # scattered indexes, leaving occasional double-length gaps;
                    # motion is a frame difference, so those gaps read as
                    # spurious spikes.
                    if max_frames == 1:
                        indexes = [indexes[0]]
                    else:
                        last = len(indexes) - 1
                        indexes = sorted({
                            indexes[round(position * last / (max_frames - 1))]
                            for position in range(max_frames)
                        })
            elif frame_count > 0:
                indexes = [0]

            sampled = 0
            for frame_index, frame in read_frames_at_indexes(capture, indexes, cv2):
                sampled += 1
                samples.append(
                    {
                        "camera": clip.get("camera", "unknown"),
                        "frame_index": int(frame_index),
                        "fps": round(fps, 3) if fps > 0 else 0.0,
                        "time_sec": round(frame_index / fps, 3) if fps > 0 else 0.0,
                        "duration_sec": round(duration_sec, 3),
                        "source_video": str(path),
                        "shape": list(frame.shape[:2]) if hasattr(frame, "shape") else [],
                        "frame": frame,
                    }
                )

            if sampled == 0:
                warnings.append(f"No frames could be sampled from video: {path}")

            clip_metrics.append(
                {
                    "camera": clip.get("camera", "unknown"),
                    "sampled_frames": sampled,
                    "duration_sec": round(duration_sec, 3),
                    "frame_count": frame_count,
                    "fps": round(fps, 3) if fps > 0 else 0.0,
                    # The rate actually achieved. Reporting the *requested*
                    # rate made diagnostics lie whenever max_frames bound --
                    # on a clip longer than ~60s every mode is capped, so
                    # thorough silently samples at balanced's rate.
                    "sample_fps": round(sampled / duration_sec, 3) if duration_sec > 0 else sample_fps,
                    "requested_sample_fps": sample_fps,
                    "source_video": str(path),
                }
            )
        finally:
            capture.release()

    return {"sampled_frames": len(samples), "samples": samples, "clip_metrics": clip_metrics}, warnings
