"""Grouping contract test for Mimir Core v2.

Run:
    python -m mimir_core_v2.test_grouping --input "C:\\mimir\\test"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .event_grouping import event_key_for_video, group_videos
from .source_discovery import discover_videos
from .validators import sorted_cameras


DEFAULT_OUTPUT = Path(r"C:\Mimir_Backend\MimirOutputV2\latest_session.json")


def normalize_path(value: str) -> str:
    return str(value or "").strip().lower().replace("/", "\\")


def group_key_for_incident(incident: dict) -> tuple[str, str]:
    return (
        normalize_path(str(incident.get("event_folder") or "")),
        str(incident.get("event_timestamp") or "").strip(),
    )


def camera_list_from_clips(clips: list[dict]) -> list[str]:
    return sorted_cameras([str(clip.get("camera") or "unknown") for clip in clips if isinstance(clip, dict)])


def read_latest_session(path: Path) -> tuple[dict | None, str]:
    if not path.exists():
        return None, f"v2 output not found: {path}"

    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"could not read v2 output: {exc}"

    if not isinstance(data, dict):
        return None, "v2 output is not a JSON object"
    return data, ""


def build_expected_groups(input_folder: str) -> tuple[list[dict], list[str], list[str]]:
    videos, warnings = discover_videos(input_folder)
    groups = group_videos(videos)
    duplicate_problems: list[str] = []
    seen: set[tuple[str, str]] = set()

    for group in groups:
        key = (normalize_path(str(group.get("event_folder") or "")), str(group.get("event_timestamp") or ""))
        if key in seen:
            duplicate_problems.append(f"internal duplicate group: folder={key[0]} timestamp={key[1]}")
        seen.add(key)

    return groups, warnings, duplicate_problems


def compare_output_to_expected(expected_groups: list[dict], session: dict) -> list[str]:
    problems: list[str] = []
    incidents = session.get("incidents")
    if not isinstance(incidents, list):
        return ["v2 output does not contain an incidents list"]

    incidents_by_key: dict[tuple[str, str], list[dict]] = {}
    for incident in incidents:
        if not isinstance(incident, dict):
            continue
        incidents_by_key.setdefault(group_key_for_incident(incident), []).append(incident)

    for key, matching_incidents in sorted(incidents_by_key.items()):
        if key[1] and len(matching_incidents) > 1:
            problems.append(
                f"output split timestamp into {len(matching_incidents)} incidents: folder={key[0]} timestamp={key[1]}"
            )

    for group in expected_groups:
        expected_count = int(group.get("camera_count") or 0)
        if expected_count <= 1:
            continue

        key = (normalize_path(str(group.get("event_folder") or "")), str(group.get("event_timestamp") or ""))
        matching_incidents = incidents_by_key.get(key, [])
        expected_cameras = set(group.get("available_cameras") or [])

        if len(matching_incidents) != 1:
            problems.append(
                f"expected one incident for folder={key[0]} timestamp={key[1]}, found {len(matching_incidents)}"
            )
            continue

        incident = matching_incidents[0]
        camera_clips = incident.get("camera_clips")
        if not isinstance(camera_clips, list):
            camera_clips = []

        actual_count = int(incident.get("camera_count") or len(camera_clips))
        actual_cameras = set(camera_list_from_clips(camera_clips))

        if actual_count != expected_count:
            problems.append(
                f"incident {incident.get('id', 'unknown')} camera_count={actual_count}, expected {expected_count}"
            )
        if not expected_cameras.issubset(actual_cameras):
            problems.append(
                f"incident {incident.get('id', 'unknown')} missing cameras: {sorted(expected_cameras - actual_cameras)}"
            )

    return problems


def print_group_summary(groups: list[dict]) -> None:
    print(f"groups found: {len(groups)}")
    for group in groups:
        print(
            "- "
            f"{group.get('event_timestamp') or Path(str(group.get('event_folder') or '')).name} | "
            f"cameras={','.join(group.get('available_cameras') or ['unknown'])} | "
            f"files={group.get('camera_count', 0)}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate Mimir Core v2 event grouping.")
    parser.add_argument("--input", required=True, help="Folder containing MP4 footage.")
    parser.add_argument("--session", default=str(DEFAULT_OUTPUT), help="Path to MimirOutputV2/latest_session.json.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    videos, discovery_warnings = discover_videos(args.input)
    groups, grouping_warnings, duplicate_problems = build_expected_groups(args.input)
    session, session_error = read_latest_session(Path(args.session))

    print("Mimir Core v2 Grouping Test")
    print("===========================")
    print(f"mp4 files found: {len(videos)}")
    print_group_summary(groups)

    problems = list(duplicate_problems)
    if session_error:
        problems.append(session_error)
    elif session is not None:
        problems.extend(compare_output_to_expected(groups, session))

    warnings = discovery_warnings + grouping_warnings
    if warnings:
        print()
        print("warnings:")
        for warning in warnings:
            print(f"- {warning}")

    print()
    print("duplicate problems:")
    if problems:
        for problem in problems:
            print(f"- {problem}")
        print()
        print("GROUPING TEST FAILED")
        return 1

    print("- none")
    print()
    print("GROUPING TEST PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

