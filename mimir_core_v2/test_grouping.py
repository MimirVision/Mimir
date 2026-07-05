"""Grouping contract test for Mimir Core v2.

Run:
    python -m mimir_core_v2.test_grouping --input "C:\\mimir\\test"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .event_grouping import GROUPING_VERSION, group_videos
from .source_discovery import discover_videos
from .validators import sorted_cameras


DEFAULT_OUTPUT = Path(r"C:\Mimir_Backend\MimirOutputV2\latest_session.json")
DEFAULT_REPORT = Path(r"C:\Mimir_Backend\MimirOutputV2\grouping_report.json")


def normalize_path(value: str) -> str:
    return str(value or "").strip().lower().replace("/", "\\")


def group_key_for_incident(incident: dict) -> tuple[str, str]:
    return (
        normalize_path(str(incident.get("event_folder") or "")),
        str(incident.get("event_timestamp") or "").strip(),
    )


def camera_list_from_clips(clips: list[dict]) -> list[str]:
    return sorted_cameras([str(clip.get("camera") or "unknown") for clip in clips if isinstance(clip, dict)])


def filename_set_from_clips(clips: list[dict]) -> set[str]:
    filenames: set[str] = set()
    for clip in clips:
        if not isinstance(clip, dict):
            continue
        filename = str(clip.get("filename") or "").strip().lower()
        if filename:
            filenames.add(filename)
    return filenames


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


def compare_output_to_expected(expected_groups: list[dict], session: dict) -> tuple[list[str], list[str]]:
    problems: list[str] = []
    passes: list[str] = []
    incidents = session.get("incidents")
    if not isinstance(incidents, list):
        return ["v2 output does not contain an incidents list"], passes

    incidents_by_key: dict[tuple[str, str], list[dict]] = {}
    for incident in incidents:
        if not isinstance(incident, dict):
            continue
        incidents_by_key.setdefault(group_key_for_incident(incident), []).append(incident)

    for key, matching_incidents in sorted(incidents_by_key.items()):
        if key[1] and len(matching_incidents) > 1:
            problems.append(
                f"FAIL: {key[1]} has output split into {len(matching_incidents)} incidents. Expected 1."
            )

    for group in expected_groups:
        expected_count = int(group.get("camera_count") or 0)
        key = (normalize_path(str(group.get("event_folder") or "")), str(group.get("event_timestamp") or ""))
        matching_incidents = incidents_by_key.get(key, [])
        expected_cameras = set(group.get("available_cameras") or [])
        expected_clips = group.get("clips") if isinstance(group.get("clips"), list) else []
        expected_filenames = filename_set_from_clips(expected_clips)

        if not key[1]:
            continue

        if len(matching_incidents) != 1:
            problems.append(
                f"FAIL: {key[1]} has {expected_count} camera files but output has {len(matching_incidents)} incidents. Expected 1."
            )
            continue

        incident = matching_incidents[0]
        camera_clips = incident.get("camera_clips")
        if not isinstance(camera_clips, list):
            camera_clips = []

        actual_count = int(incident.get("camera_count") or len(camera_clips))
        actual_cameras = set(camera_list_from_clips(camera_clips))
        actual_filenames = filename_set_from_clips(camera_clips)

        if actual_count != expected_count:
            problems.append(
                f"FAIL: {key[1]} incident {incident.get('id', 'unknown')} camera_count={actual_count}, expected {expected_count}."
            )
        if not expected_cameras.issubset(actual_cameras):
            problems.append(
                f"FAIL: {key[1]} incident {incident.get('id', 'unknown')} missing cameras: {sorted(expected_cameras - actual_cameras)}."
            )
        if not expected_filenames.issubset(actual_filenames):
            problems.append(
                f"FAIL: {key[1]} incident {incident.get('id', 'unknown')} missing camera files: {sorted(expected_filenames - actual_filenames)}."
            )

        if actual_count == expected_count and expected_cameras.issubset(actual_cameras) and expected_filenames.issubset(actual_filenames):
            passes.append(f"PASS: {key[1]} grouped into 1 incident with {expected_count} cameras.")

    return problems, passes


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
    parser.add_argument("--report", default=str(DEFAULT_REPORT), help="Path to write grouping_report.json.")
    return parser


def write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(report, file, indent=2)


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
    passes: list[str] = []
    if session_error:
        problems.append(session_error)
    elif session is not None:
        compare_problems, passes = compare_output_to_expected(groups, session)
        problems.extend(compare_problems)

    warnings = discovery_warnings + grouping_warnings
    if warnings:
        print()
        print("warnings:")
        for warning in warnings:
            print(f"- {warning}")

    print()
    print("grouping results:")
    if problems:
        for problem in problems:
            print(f"- {problem}")
        print()
        verdict = "GROUPING FAILED"
    else:
        if passes:
            for passed in passes:
                print(f"- {passed}")
        else:
            print("- No timestamp groups with multiple cameras found in this input.")
        print()
        verdict = "GROUPING OK"

    report = {
        "grouping_version": GROUPING_VERSION,
        "input": args.input,
        "mp4_files_found": len(videos),
        "groups_found": len(groups),
        "passes": passes,
        "problems": problems,
        "warnings": warnings,
        "verdict": verdict,
    }
    write_report(Path(args.report), report)
    print(f"Report written: {Path(args.report)}")
    print()
    print(verdict)

    if problems:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
