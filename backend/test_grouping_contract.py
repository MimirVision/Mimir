import argparse
import json
import re
import sys
from pathlib import Path


BASE_DIR = Path(r"C:\Mimir_Backend")
LATEST_SESSION_JSON = BASE_DIR / "MimirOutput" / "latest_session.json"
REPORT_JSON = BASE_DIR / "MimirOutput" / "grouping_contract_report.json"

TESLA_FILENAME_RE = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})-(?P<camera>.+)\.mp4$",
    re.IGNORECASE,
)

CAMERA_ALIASES = {
    "front": "front",
    "back": "back",
    "rear": "back",
    "left": "left_repeater",
    "right": "right_repeater",
    "left_repeater": "left_repeater",
    "right_repeater": "right_repeater",
    "left_pillar": "left_pillar",
    "right_pillar": "right_pillar",
}


def norm_path(value):
    try:
        return str(Path(str(value)).resolve()).lower()
    except Exception:
        return str(value or "").replace("/", "\\").lower()


def parse_tesla_filename(path):
    name = Path(path).name
    match = TESLA_FILENAME_RE.match(name)

    if not match:
        return None

    raw_camera = match.group("camera").lower()
    camera = CAMERA_ALIASES.get(raw_camera)

    if not camera:
        return None

    return {
        "timestamp": match.group("timestamp"),
        "camera": camera,
        "raw_camera": raw_camera,
    }


def discover_mp4_files(input_folder):
    root = Path(input_folder)
    if not root.exists() or not root.is_dir():
        raise FileNotFoundError(f"Input folder not found: {input_folder}")

    return sorted(path for path in root.rglob("*.mp4") if path.is_file())


def group_input_files(files):
    groups = {}

    for path in files:
        parsed = parse_tesla_filename(path)
        if not parsed:
            continue

        group_key = f"{norm_path(path.parent)}|{parsed['timestamp']}"
        group = groups.setdefault(
            group_key,
            {
                "group_key": group_key,
                "event_folder": str(path.parent),
                "timestamp": parsed["timestamp"],
                "files": [],
                "cameras": [],
            },
        )
        group["files"].append(str(path))
        if parsed["camera"] not in group["cameras"]:
            group["cameras"].append(parsed["camera"])

    for group in groups.values():
        group["files"].sort()
        group["cameras"].sort()

    return groups


def read_latest_session(path):
    if not path.exists():
        return None

    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, dict) else None
    except Exception as exc:
        return {"_read_error": str(exc), "incidents": []}


def camera_clips(incident):
    raw = incident.get("camera_clips")
    if isinstance(raw, list):
        return [clip for clip in raw if isinstance(clip, dict)]
    if isinstance(raw, dict):
        clips = []
        for camera, value in raw.items():
            if isinstance(value, str):
                clips.append({"camera": camera, "path": value, "filename": Path(value).name})
            elif isinstance(value, dict):
                clip = dict(value)
                clip.setdefault("camera", camera)
                clips.append(clip)
        return clips
    return []


def incident_referenced_paths(incident):
    values = set()

    for key in [
        "source_video",
        "source_clip",
        "video_path",
        "original_source_video",
        "library_video_path",
        "trash_video_path",
    ]:
        value = incident.get(key)
        if isinstance(value, str) and value.strip():
            values.add(norm_path(value))

    for clip in camera_clips(incident):
        for key in [
            "path",
            "filename",
            "video_path",
            "source_video",
            "source_clip",
            "original_source_video",
            "library_path",
            "trash_path",
        ]:
            value = clip.get(key)
            if isinstance(value, str) and value.strip():
                values.add(norm_path(value))
                values.add(value.lower())

    return values


def incident_matches_group(incident, group):
    incident_values = incident_referenced_paths(incident)
    if not incident_values:
        return False

    for file_path in group["files"]:
        normalized = norm_path(file_path)
        basename = Path(file_path).name.lower()
        if normalized in incident_values or basename in incident_values:
            return True

    timestamp = group.get("timestamp", "")
    if timestamp:
        for value in [
            incident.get("event_group_id"),
            incident.get("event_timestamp"),
            incident.get("source_event_timestamp"),
            incident.get("tesla_event_timestamp"),
        ]:
            if isinstance(value, str) and timestamp in value:
                return True

    return False


def compare_groups_to_session(groups, session):
    incidents = []
    if isinstance(session, dict):
        raw_incidents = session.get("incidents", [])
        if isinstance(raw_incidents, list):
            incidents = [incident for incident in raw_incidents if isinstance(incident, dict)]

    results = []
    broken = False

    for group in groups.values():
        matched_incidents = [
            incident for incident in incidents if incident_matches_group(incident, group)
        ]
        incident_ids = [str(incident.get("id", "")) for incident in matched_incidents]
        produced_count = len(matched_incidents)
        ok = produced_count <= 1

        if not ok:
            broken = True

        results.append(
            {
                "timestamp": group["timestamp"],
                "event_folder": group["event_folder"],
                "camera_count": len(group["cameras"]),
                "cameras": group["cameras"],
                "files": group["files"],
                "matched_incident_count": produced_count,
                "matched_incident_ids": incident_ids,
                "ok": ok,
            }
        )

    return results, broken


def print_group_summary(files, groups):
    print("Mimir Grouping Contract")
    print("=======================")
    print(f"total mp4 files: {len(files)}")
    print(f"total timestamp groups: {len(groups)}")

    for group in groups.values():
        print("")
        print(f"group key: {group['group_key']}")
        print(f"timestamp: {group['timestamp']}")
        print(f"event folder: {group['event_folder']}")
        print(f"cameras: {', '.join(group['cameras']) or 'none'}")
        print("files:")
        for file_path in group["files"]:
            print(f"  - {file_path}")


def print_results(results, session_exists):
    print("")
    print("Scanner Output Comparison")
    print("=========================")

    if not session_exists:
        print(f"latest_session.json not found: {LATEST_SESSION_JSON}")
        print("Input grouping was reported, but scanner output could not be compared.")
        return

    if not results:
        print("No Tesla-style timestamp groups found to compare.")
        return

    for result in results:
        if result["ok"]:
            print(
                "PASS: timestamp "
                f"{result['timestamp']} grouped into "
                f"{result['matched_incident_count']} incident(s) "
                f"with {result['camera_count']} cameras."
            )
        else:
            print(
                "FAIL: timestamp "
                f"{result['timestamp']} has {len(result['files'])} camera files "
                f"but scanner produced {result['matched_incident_count']} incidents. "
                "Expected 1."
            )
            print(f"  incident ids: {', '.join(result['matched_incident_ids'])}")


def write_report(report):
    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    with REPORT_JSON.open("w", encoding="utf-8") as file:
        json.dump(report, file, indent=2)


def main():
    parser = argparse.ArgumentParser(description="Check Tesla camera grouping contract.")
    parser.add_argument("--input", required=True, help="Folder containing TeslaCam MP4 files.")
    args = parser.parse_args()

    files = discover_mp4_files(args.input)
    groups = group_input_files(files)
    session = read_latest_session(LATEST_SESSION_JSON)
    session_exists = session is not None
    results, broken = compare_groups_to_session(groups, session)

    print_group_summary(files, groups)
    print_results(results, session_exists)

    report = {
        "input_folder": str(Path(args.input)),
        "total_mp4_files": len(files),
        "total_timestamp_groups": len(groups),
        "latest_session_path": str(LATEST_SESSION_JSON),
        "latest_session_found": session_exists,
        "grouping_broken": bool(broken),
        "groups": list(groups.values()),
        "comparison": results,
    }
    if isinstance(session, dict) and session.get("_read_error"):
        report["latest_session_read_error"] = session.get("_read_error")

    write_report(report)
    print("")
    print(f"Report written: {REPORT_JSON}")

    return 1 if broken else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"ERROR: {exc}")
        sys.exit(2)
