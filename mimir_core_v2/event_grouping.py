"""Turn camera files into event groups.

Core v2 rule: a camera file is not an incident. A timestamp/event folder is an
incident, with camera files attached as angles.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from .validators import sorted_cameras, stable_id_text


def event_key_for_video(video: dict) -> tuple[str, str]:
    event_timestamp = str(video.get("event_timestamp") or "").strip()
    source_folder = str(video.get("source_folder") or "")

    if event_timestamp:
        return (source_folder, event_timestamp)

    filename_stem = Path(str(video.get("filename") or "unknown")).stem
    return (source_folder, filename_stem)


def _group_id(source_folder: str, event_timestamp: str, source_category: str) -> str:
    digest = hashlib.sha1(f"{source_folder}|{event_timestamp}|{source_category}".encode("utf-8")).hexdigest()[:8]
    readable = stable_id_text(event_timestamp or Path(source_folder).name)
    return f"event_{readable}_{digest}"


def group_videos(videos: list[dict]) -> list[dict]:
    grouped: dict[tuple[str, str], list[dict]] = {}
    for video in videos:
        grouped.setdefault(event_key_for_video(video), []).append(video)

    event_groups: list[dict] = []
    for (source_folder, event_timestamp), clips in sorted(grouped.items()):
        source_category = str(clips[0].get("source_category") or "generic_folder") if clips else "generic_folder"
        cameras = sorted_cameras([str(clip.get("camera") or "unknown") for clip in clips])
        sorted_clips = sorted(
            clips,
            key=lambda clip: (cameras.index(clip.get("camera")) if clip.get("camera") in cameras else 999, clip.get("filename", "")),
        )
        event_groups.append(
            {
                "event_group_id": _group_id(source_folder, event_timestamp, source_category),
                "event_timestamp": event_timestamp,
                "event_folder": source_folder,
                "source_category": source_category,
                "clips": [
                    {
                        "camera": clip.get("camera") or "unknown",
                        "path": clip.get("path") or "",
                        "filename": clip.get("filename") or "",
                        "duration_sec": clip.get("duration_sec", 0.0),
                        "exists": bool(clip.get("exists")),
                    }
                    for clip in sorted_clips
                ],
                "available_cameras": cameras,
                "camera_count": len(sorted_clips),
            }
        )

    return event_groups
