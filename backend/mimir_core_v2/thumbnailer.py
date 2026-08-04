"""JPEG thumbnail generation for Mimir Core v2 incidents.

Thumbnail generation is best-effort output enrichment only. Failures are
reported as warnings and must not prevent incident creation.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


THUMBNAIL_VERSION = "thumbnailer_v2_0_1"
CONTACT_SHEET_MIN_FRAMES = 3
CONTACT_SHEET_MAX_FRAMES = 6
CONTACT_TILE_SIZE = (320, 180)
JPEG_QUALITY = 88


def _load_cv2() -> Any:
    try:
        import cv2  # type: ignore

        return cv2
    except Exception:
        return None


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_name(value: Any, fallback: str) -> str:
    text = str(value or "").strip() or fallback
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", text)
    return text.strip("._") or fallback


def _absolute_path(path: Path) -> str:
    return str(path.resolve())


def _primary_clip(event_group: dict, primary_camera: str) -> dict:
    clips = event_group.get("clips") if isinstance(event_group.get("clips"), list) else []
    for clip in clips:
        if isinstance(clip, dict) and clip.get("camera") == primary_camera:
            return clip
    for clip in clips:
        if isinstance(clip, dict):
            return clip
    return {}


def _primary_camera(event_group: dict, evidence: dict, severity: dict) -> str:
    camera = str(severity.get("primary_camera") or "").strip()
    if camera:
        return camera

    camera = str(evidence.get("primary_camera_candidate") or "").strip()
    if camera:
        return camera

    clip = _primary_clip(event_group, "")
    return str(clip.get("camera") or "unknown")


def _sample_frames_for_camera(sample_result: dict, primary_camera: str) -> list[dict]:
    frames: list[dict] = []
    for sample in sample_result.get("samples", []):
        if not isinstance(sample, dict):
            continue
        if sample.get("camera") != primary_camera:
            continue
        if sample.get("frame") is None:
            continue
        frames.append(
            {
                "time_sec": _safe_float(sample.get("time_sec")),
                "frame": sample.get("frame"),
            }
        )
    frames.sort(key=lambda item: item["time_sec"])
    return frames


def _unique_indexes(indexes: list[int], count: int) -> list[int]:
    unique: list[int] = []
    for index in indexes:
        bounded = max(0, min(count - 1, index))
        if bounded not in unique:
            unique.append(bounded)
    return unique


def _nearest_sample_index(frames: list[dict], target_time_sec: float) -> int:
    return min(
        range(len(frames)),
        key=lambda index: abs(_safe_float(frames[index].get("time_sec")) - target_time_sec),
    )


def _sample_based_frames(frames: list[dict], spike_time_sec: float) -> tuple[Any | None, list[Any]]:
    if not frames:
        return None, []

    if spike_time_sec > 0:
        hero_index = _nearest_sample_index(frames, spike_time_sec)
    else:
        hero_index = len(frames) // 2
    hero_frame = frames[hero_index].get("frame")

    if spike_time_sec > 0:
        target_times = [
            max(0.0, spike_time_sec - 3.0),
            max(0.0, spike_time_sec - 1.5),
            spike_time_sec,
            spike_time_sec + 1.5,
            spike_time_sec + 3.0,
        ]
        indexes = [_nearest_sample_index(frames, target) for target in target_times]
    else:
        max_index = len(frames) - 1
        count = min(CONTACT_SHEET_MAX_FRAMES, max(CONTACT_SHEET_MIN_FRAMES, len(frames)))
        if len(frames) == 1:
            indexes = [0]
        else:
            indexes = [round(position * max_index / (count - 1)) for position in range(count)]

    indexes = _unique_indexes(indexes, len(frames))
    if len(indexes) < min(CONTACT_SHEET_MIN_FRAMES, len(frames)):
        for index in range(len(frames)):
            if index not in indexes:
                indexes.append(index)
            if len(indexes) >= min(CONTACT_SHEET_MIN_FRAMES, len(frames)):
                break
    indexes = indexes[:CONTACT_SHEET_MAX_FRAMES]
    return hero_frame, [frames[index].get("frame") for index in indexes]


def _read_frame_at(capture: Any, cv2: Any, frame_index: int) -> Any | None:
    capture.set(cv2.CAP_PROP_POS_FRAMES, max(0, frame_index))
    ok, frame = capture.read()
    if ok and frame is not None:
        return frame
    return None


def _first_readable_frame(capture: Any, cv2: Any, frame_count: int) -> Any | None:
    capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
    attempts = frame_count if frame_count > 0 else 30
    for _ in range(min(attempts, 60)):
        ok, frame = capture.read()
        if ok and frame is not None:
            return frame
    return None


def _evenly_spaced_indexes(frame_count: int, count: int) -> list[int]:
    if frame_count <= 1:
        return [0]
    count = max(1, min(count, frame_count))
    return [round(position * (frame_count - 1) / (count - 1)) for position in range(count)]


def _video_based_frames(path: Path, spike_time_sec: float) -> tuple[Any | None, list[Any], list[str]]:
    cv2 = _load_cv2()
    warnings: list[str] = []
    if cv2 is None:
        return None, [], ["OpenCV is not available; thumbnail generation skipped."]
    if not path.exists():
        return None, [], [f"Thumbnail video file is missing: {path}"]

    capture = cv2.VideoCapture(str(path))
    try:
        if not capture.isOpened():
            return None, [], [f"Could not open video for thumbnail generation: {path}"]

        frame_count = _safe_int(capture.get(cv2.CAP_PROP_FRAME_COUNT), 0)
        fps = _safe_float(capture.get(cv2.CAP_PROP_FPS), 0.0)
        duration_sec = frame_count / fps if frame_count > 0 and fps > 0 else 0.0

        hero_frame = None
        if spike_time_sec > 0 and fps > 0:
            hero_frame = _read_frame_at(capture, cv2, round(spike_time_sec * fps))
        if hero_frame is None and frame_count > 0:
            hero_frame = _read_frame_at(capture, cv2, frame_count // 2)
        if hero_frame is None:
            hero_frame = _first_readable_frame(capture, cv2, frame_count)

        contact_frames: list[Any] = []
        if spike_time_sec > 0 and fps > 0:
            target_times = [
                max(0.0, spike_time_sec - 3.0),
                max(0.0, spike_time_sec - 1.5),
                spike_time_sec,
                min(duration_sec, spike_time_sec + 1.5) if duration_sec else spike_time_sec + 1.5,
                min(duration_sec, spike_time_sec + 3.0) if duration_sec else spike_time_sec + 3.0,
            ]
            indexes = _unique_indexes([round(time_sec * fps) for time_sec in target_times], max(frame_count, 1))
        else:
            indexes = _evenly_spaced_indexes(frame_count, CONTACT_SHEET_MAX_FRAMES)

        for index in indexes[:CONTACT_SHEET_MAX_FRAMES]:
            frame = _read_frame_at(capture, cv2, index)
            if frame is not None:
                contact_frames.append(frame)

        if not contact_frames and hero_frame is not None:
            contact_frames.append(hero_frame)

        return hero_frame, contact_frames, warnings
    finally:
        capture.release()


def _write_jpeg(path: Path, frame: Any) -> bool:
    cv2 = _load_cv2()
    if cv2 is None or frame is None:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    return bool(cv2.imwrite(str(path), frame, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY]))


def _contact_sheet(frames: list[Any]) -> Any | None:
    cv2 = _load_cv2()
    if cv2 is None or not frames:
        return None

    tiles: list[Any] = []
    for frame in frames[:CONTACT_SHEET_MAX_FRAMES]:
        if frame is None:
            continue
        try:
            tiles.append(cv2.resize(frame, CONTACT_TILE_SIZE))
        except Exception:
            continue

    if not tiles:
        return None

    rows: list[Any] = []
    columns = 3 if len(tiles) > 2 else len(tiles)
    for start in range(0, len(tiles), columns):
        row_tiles = tiles[start : start + columns]
        while len(row_tiles) < columns:
            row_tiles.append(row_tiles[-1])
        rows.append(cv2.hconcat(row_tiles))
    return cv2.vconcat(rows) if len(rows) > 1 else rows[0]


def generate_thumbnails(
    event_group: dict,
    evidence: dict,
    severity: dict,
    sample_result: dict,
    output_dir: str | Path,
    incident_id: str,
) -> dict:
    """Generate best-effort hero thumbnail and contact sheet for one incident."""

    warnings: list[str] = []
    output_folder = Path(output_dir) / "thumbnails"
    primary_camera = _primary_camera(event_group, evidence, severity)
    primary_clip = _primary_clip(event_group, primary_camera)
    video_path = Path(str(primary_clip.get("path") or ""))
    spike_time_sec = _safe_float(evidence.get("motion_spike_time_sec"))
    used_camera = primary_camera

    sampled_frames = _sample_frames_for_camera(sample_result, primary_camera)
    hero_frame, contact_frames = _sample_based_frames(sampled_frames, spike_time_sec)
    if hero_frame is None:
        hero_frame, contact_frames, read_warnings = _video_based_frames(video_path, spike_time_sec)
        warnings.extend(read_warnings)
    if hero_frame is None:
        clips = event_group.get("clips") if isinstance(event_group.get("clips"), list) else []
        for clip in clips:
            if not isinstance(clip, dict):
                continue
            camera = str(clip.get("camera") or "unknown")
            if camera == primary_camera:
                continue
            sampled_frames = _sample_frames_for_camera(sample_result, camera)
            hero_frame, contact_frames = _sample_based_frames(sampled_frames, spike_time_sec)
            if hero_frame is not None:
                used_camera = camera
                warnings.append(f"Primary camera thumbnail unavailable; used {camera} instead.")
                break
    if hero_frame is None:
        clips = event_group.get("clips") if isinstance(event_group.get("clips"), list) else []
        for clip in clips:
            if not isinstance(clip, dict):
                continue
            camera = str(clip.get("camera") or "unknown")
            if camera == primary_camera:
                continue
            alternate_path = Path(str(clip.get("path") or ""))
            hero_frame, contact_frames, read_warnings = _video_based_frames(alternate_path, spike_time_sec)
            warnings.extend(read_warnings)
            if hero_frame is not None:
                used_camera = camera
                warnings.append(f"Primary camera thumbnail unavailable; used {camera} instead.")
                break

    result = {
        "thumbnail": None,
        "hero_thumbnail": None,
        "contact_sheet": None,
        "generated": 0,
        "failed": 0,
        "warnings": warnings,
        "thumbnail_output_dir": _absolute_path(output_folder),
        "primary_camera": used_camera,
    }

    if hero_frame is None:
        result["failed"] = 1
        result["warnings"].append(f"No readable frame found for thumbnails: {video_path}")
        return result

    base = _safe_name(f"{incident_id}_{event_group.get('event_timestamp')}_{used_camera}", incident_id)
    hero_path = output_folder / f"{base}_hero.jpg"
    contact_path = output_folder / f"{base}_contact_sheet.jpg"

    if _write_jpeg(hero_path, hero_frame) and hero_path.exists():
        result["hero_thumbnail"] = _absolute_path(hero_path)
        result["thumbnail"] = _absolute_path(hero_path)
        result["generated"] = int(result["generated"]) + 1
    else:
        result["failed"] = int(result["failed"]) + 1
        result["warnings"].append(f"Could not write hero thumbnail: {hero_path}")

    sheet = _contact_sheet(contact_frames)
    if sheet is not None and _write_jpeg(contact_path, sheet) and contact_path.exists():
        result["contact_sheet"] = _absolute_path(contact_path)
        result["generated"] = int(result["generated"]) + 1
    else:
        result["failed"] = int(result["failed"]) + 1
        result["warnings"].append(f"Could not write contact sheet: {contact_path}")

    return result
