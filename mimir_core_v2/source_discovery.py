"""Find source videos without changing or moving them."""

from __future__ import annotations

from pathlib import Path

from .validators import parse_tesla_filename, source_category_for_path


VIDEO_EXTENSIONS = {".mp4"}


def discover_videos(input_folder: str | Path) -> tuple[list[dict], list[str]]:
    root = Path(input_folder).expanduser()
    warnings: list[str] = []

    if not root.exists():
        return [], [f"Input folder does not exist: {root}"]
    if not root.is_dir():
        return [], [f"Input path is not a folder: {root}"]

    videos: list[dict] = []
    try:
        paths = sorted(
            path
            for path in root.rglob("*")
            if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
        )
    except OSError as exc:
        return [], [f"Could not read input folder: {exc}"]

    for path in paths:
        parsed = parse_tesla_filename(path.name)
        videos.append(
            {
                "path": str(path.resolve()),
                "filename": path.name,
                "source_folder": str(path.parent.resolve()),
                "source_category": source_category_for_path(path),
                "event_timestamp": parsed["event_timestamp"],
                "camera": parsed["camera"],
                "is_tesla_style": parsed["is_tesla_style"],
                "exists": path.exists(),
                "duration_sec": 0.0,
            }
        )

    return videos, warnings

