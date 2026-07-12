"""Turn camera files into event groups.

Core v2 rule: a camera file is not an incident. A timestamp/event folder is an
incident, with camera files attached as angles.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .validators import sorted_cameras, stable_id_text


GROUPING_VERSION = "event_grouping_v2_0_2"


def _format_display_timestamp(value: str) -> str:
    text = str(value or "").strip()
    if len(text) != 19:
        return ""
    if text[4] != "-" or text[7] != "-" or text[10] not in {"_", "-"}:
        return ""
    return f"{text[:10]} {text[11:].replace('-', ':')}"


def _clean_display_title(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+\(\d+\)$", "", text)
    text = re.sub(r"\.(mp4|mov|avi|mkv)\s*(?=,|$)", "", text, flags=re.IGNORECASE)
    parts = [part.strip() for part in text.split(",") if part.strip()]
    if parts:
        text = " - ".join(parts)
    return re.sub(r"\s+", " ", text).strip()


def _clip_source_metadata(sorted_clips: list[dict], event_timestamp: str) -> dict:
    primary_clip = sorted_clips[0] if sorted_clips else {}
    source_filename = str(primary_clip.get("filename") or "").strip()
    source_stem = Path(source_filename).stem if source_filename else ""
    parsed_timestamp = str(primary_clip.get("event_timestamp") or "").strip()
    tesla_timestamp = any(bool(clip.get("is_tesla_style")) for clip in sorted_clips)
    filename_timestamp_detected = bool(parsed_timestamp)
    display_timestamp = _format_display_timestamp(parsed_timestamp) if filename_timestamp_detected else ""
    display_title = display_timestamp if tesla_timestamp and display_timestamp else _clean_display_title(source_stem)

    return {
        "display_title": display_title,
        "display_timestamp": display_timestamp,
        "source_filename": source_filename,
        "source_stem": source_stem,
        "filename_timestamp_detected": filename_timestamp_detected,
        "tesla_timestamp_detected": tesla_timestamp,
        "generic_filename_group": not tesla_timestamp,
    }


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
        source_metadata = _clip_source_metadata(sorted_clips, event_timestamp)
        event_groups.append(
            {
                "event_group_id": _group_id(source_folder, event_timestamp, source_category),
                "event_timestamp": event_timestamp,
                **source_metadata,
                "event_folder": source_folder,
                "source_category": source_category,
                "clips": [
                    {
                        "camera": clip.get("camera") or "unknown",
                        "path": clip.get("path") or "",
                        "filename": clip.get("filename") or "",
                        "is_tesla_style": bool(clip.get("is_tesla_style")),
                        "event_timestamp": clip.get("event_timestamp") or "",
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


def build_grouping_debug(event_groups: list[dict], warnings: list[str] | None = None) -> dict:
    unknown_camera_files: list[str] = []
    ungrouped_files: list[str] = []
    generic_filename_groups = 0
    tesla_timestamp_groups = 0

    for group in event_groups:
        clips = group.get("clips") if isinstance(group.get("clips"), list) else []
        if group.get("generic_filename_group"):
            generic_filename_groups += 1
        if group.get("tesla_timestamp_detected"):
            tesla_timestamp_groups += 1
        if not group.get("event_timestamp"):
            ungrouped_files.extend(str(clip.get("filename") or "") for clip in clips if isinstance(clip, dict))
        for clip in clips:
            if isinstance(clip, dict) and clip.get("camera") == "unknown":
                unknown_camera_files.append(str(clip.get("filename") or ""))

    mp4_files_found = sum(int(group.get("camera_count") or 0) for group in event_groups)
    multi_camera_groups = sum(1 for group in event_groups if int(group.get("camera_count") or 0) > 1)
    single_camera_groups = sum(1 for group in event_groups if int(group.get("camera_count") or 0) == 1)

    grouping_warnings = list(warnings or [])
    if not event_groups:
        grouping_warnings.append("No event groups were built from the selected input.")

    return {
        "mp4_files_found": mp4_files_found,
        "event_groups_built": len(event_groups),
        "multi_camera_groups": multi_camera_groups,
        "single_camera_groups": single_camera_groups,
        "generic_filename_groups": generic_filename_groups,
        "tesla_timestamp_groups": tesla_timestamp_groups,
        "unknown_camera_files": unknown_camera_files,
        "ungrouped_files": ungrouped_files,
        "grouping_warnings": grouping_warnings,
    }
