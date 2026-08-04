"""Shared validation and parsing helpers for Mimir Core v2."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


CAMERA_ALIASES = {
    "front": "front",
    "back": "back",
    "rear": "back",
    "left": "left",
    "right": "right",
    "left_repeater": "left_repeater",
    "right_repeater": "right_repeater",
    "left_pillar": "left_pillar",
    "right_pillar": "right_pillar",
}

CAMERA_PRIORITY = [
    "front",
    "back",
    "left_repeater",
    "right_repeater",
    "left",
    "right",
    "left_pillar",
    "right_pillar",
    "unknown",
]

TESLA_FILENAME_RE = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})-"
    r"(?P<camera>front|back|rear|left|right|left_repeater|right_repeater|left_pillar|right_pillar)\.mp4$",
    re.IGNORECASE,
)

GENERIC_TIMESTAMP_RE = re.compile(
    r"(?P<timestamp>\d{4}-\d{2}-\d{2}[_-]\d{2}-\d{2}-\d{2})",
    re.IGNORECASE,
)


def normalize_camera(camera: Any) -> str:
    text = str(camera or "").strip().lower()
    return CAMERA_ALIASES.get(text, "unknown")


def parse_tesla_filename(filename: str) -> dict[str, str]:
    match = TESLA_FILENAME_RE.match(filename)
    if not match:
        generic = GENERIC_TIMESTAMP_RE.search(filename)
        return {
            "event_timestamp": generic.group("timestamp") if generic else "",
            "camera": "unknown",
            "is_tesla_style": False,
        }

    return {
        "event_timestamp": match.group("timestamp"),
        "camera": normalize_camera(match.group("camera")),
        "is_tesla_style": True,
    }


def source_category_for_path(path: Path) -> str:
    parts = {part.lower() for part in path.parts}
    if "sentryclips" in parts:
        return "SentryClips"
    if "savedclips" in parts:
        return "SavedClips"
    if "recentclips" in parts:
        return "RecentClips"
    return "generic_folder"


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def stable_id_text(value: str) -> str:
    safe = []
    for char in value:
        if char.isalnum() or char in {"-", "_"}:
            safe.append(char)
        else:
            safe.append("_")
    return "".join(safe).strip("_") or "event"


def sorted_cameras(cameras: list[str]) -> list[str]:
    unique = []
    for camera in cameras:
        normalized = normalize_camera(camera)
        if normalized not in unique:
            unique.append(normalized)

    def sort_key(camera: str) -> tuple[int, str]:
        try:
            return (CAMERA_PRIORITY.index(camera), camera)
        except ValueError:
            return (len(CAMERA_PRIORITY), camera)

    return sorted(unique, key=sort_key)
