import argparse
import json
import re
from collections import Counter
from pathlib import Path


BACKEND_ROOT = Path(r"C:\Mimir_Backend")
OUTPUT_PATH = BACKEND_ROOT / "MimirOutput" / "discovered_source.json"
TESLACAM_FOLDERS = {"RecentClips", "SavedClips", "SentryClips"}
COMMON_CAMERAS = ["front", "back", "left_repeater", "right_repeater"]
SUPPORTED_CAMERAS = {
    "front",
    "back",
    "left_repeater",
    "right_repeater",
    "left_pillar",
    "right_pillar",
    "left",
    "right",
    "rear",
}
TESLACAM_PATTERN = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})-(?P<camera>.+)\.mp4$",
    re.IGNORECASE,
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Discover TeslaCam or generic footage from a selected folder."
    )
    parser.add_argument("--input", required=True, help="Selected folder to inspect.")
    return parser.parse_args()


def norm_path(path):
    return str(Path(path))


def safe_resolve(path):
    try:
        return path.resolve()
    except OSError:
        return path.absolute()


def is_mp4(path):
    return path.is_file() and path.suffix.lower() == ".mp4"


def find_child_case_insensitive(folder, child_name):
    try:
        for child in folder.iterdir():
            if child.is_dir() and child.name.lower() == child_name.lower():
                return child
    except OSError:
        return None
    return None


def nearest_teslacam_root(path):
    current = path if path.is_dir() else path.parent

    while True:
        if current.name.lower() == "teslacam":
            return current

        if current.parent == current:
            return None

        current = current.parent


def source_category_for(path):
    parts = path.parts
    for part in reversed(parts):
        for folder in TESLACAM_FOLDERS:
            if part.lower() == folder.lower():
                return folder
    return "Generic"


def folder_is_or_inside_teslacam_subfolder(path):
    current = path
    while current.parent != current:
        if any(current.name.lower() == folder.lower() for folder in TESLACAM_FOLDERS):
            return True
        current = current.parent
    return False


def folder_is_teslacam_category(path):
    return any(path.name.lower() == folder.lower() for folder in TESLACAM_FOLDERS)


def classify_input(selected):
    if not selected.exists() or not selected.is_dir():
        return {
            "detected_source_type": "missing",
            "drive_root": None,
            "teslacam_root": None,
            "scan_roots": [],
        }

    teslacam_child = find_child_case_insensitive(selected, "TeslaCam")
    if teslacam_child:
        return {
            "detected_source_type": "drive_root",
            "drive_root": selected,
            "teslacam_root": teslacam_child,
            "scan_roots": teslacam_scan_roots(teslacam_child),
        }

    if selected.name.lower() == "teslacam":
        return {
            "detected_source_type": "teslacam_root",
            "drive_root": selected.parent,
            "teslacam_root": selected,
            "scan_roots": teslacam_scan_roots(selected),
        }

    root = nearest_teslacam_root(selected)
    direct_mp4s = []
    try:
        direct_mp4s = [path for path in selected.iterdir() if is_mp4(path)]
    except OSError:
        direct_mp4s = []

    event_json = selected / "event.json"
    if folder_is_teslacam_category(selected):
        return {
            "detected_source_type": "teslacam_subfolder",
            "drive_root": root.parent if root else None,
            "teslacam_root": root,
            "scan_roots": [selected],
        }

    if folder_is_or_inside_teslacam_subfolder(selected) and (direct_mp4s or event_json.exists()):
        return {
            "detected_source_type": "event_folder",
            "drive_root": root.parent if root else None,
            "teslacam_root": root,
            "scan_roots": [selected],
        }

    if folder_is_or_inside_teslacam_subfolder(selected):
        return {
            "detected_source_type": "teslacam_subfolder",
            "drive_root": root.parent if root else None,
            "teslacam_root": root,
            "scan_roots": [selected],
        }

    if direct_mp4s:
        return {
            "detected_source_type": "event_folder" if event_json.exists() else "generic_folder",
            "drive_root": root.parent if root else None,
            "teslacam_root": root,
            "scan_roots": [selected],
        }

    return {
        "detected_source_type": "generic_folder",
        "drive_root": root.parent if root else None,
        "teslacam_root": root,
        "scan_roots": [selected],
    }


def teslacam_scan_roots(teslacam_root):
    roots = []
    for folder in ("RecentClips", "SavedClips", "SentryClips"):
        child = find_child_case_insensitive(teslacam_root, folder)
        if child:
            roots.append(child)
    return roots or [teslacam_root]


def discover_mp4_files(scan_roots):
    files = []
    for root in scan_roots:
        try:
            files.extend(path for path in root.rglob("*") if is_mp4(path))
        except OSError:
            continue
    return sorted(files, key=lambda path: str(path).lower())


def parse_teslacam_filename(path):
    match = TESLACAM_PATTERN.match(path.name)
    if not match:
        return None, None

    timestamp = match.group("timestamp")
    camera = match.group("camera").lower()
    return timestamp, camera


def find_event_json(folder):
    event_json = folder / "event.json"
    return event_json if event_json.exists() and event_json.is_file() else None


def find_thumb(folder):
    for name in ("thumb.png", "thumb.jpg", "thumbnail.png", "thumbnail.jpg"):
        candidate = folder / name
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def read_event_json(path):
    empty = {
        "event_timestamp": None,
        "event_reason": None,
        "city": None,
        "est_lat": None,
        "est_lon": None,
        "raw_event_json": None,
    }

    if not path:
        return empty

    try:
        with path.open("r", encoding="utf-8") as file:
            raw = json.load(file)
    except (OSError, json.JSONDecodeError):
        return empty

    if not isinstance(raw, dict):
        return empty

    return {
        "event_timestamp": raw.get("timestamp"),
        "event_reason": raw.get("reason"),
        "city": raw.get("city"),
        "est_lat": raw.get("est_lat"),
        "est_lon": raw.get("est_lon"),
        "raw_event_json": raw,
    }


def event_folder_for(file_path, source_category):
    parent = file_path.parent
    if source_category in TESLACAM_FOLDERS and parent.name == source_category:
        return None
    return parent


def event_group_key(file_path):
    source_category = source_category_for(file_path)
    timestamp, camera = parse_teslacam_filename(file_path)
    event_folder = event_folder_for(file_path, source_category)

    if timestamp:
        event_id = timestamp
    else:
        event_id = file_path.stem

    return (
        source_category,
        str(event_folder or file_path.parent),
        event_id,
        timestamp,
        camera,
    )


def build_event_groups(files):
    grouped = {}
    unknown_cameras = Counter()

    for file_path in files:
        source_category, folder, event_id, timestamp, camera = event_group_key(file_path)
        group_key = (source_category, folder, event_id)

        if group_key not in grouped:
            folder_path = Path(folder)
            event_json_path = find_event_json(folder_path)
            thumb_path = find_thumb(folder_path)
            grouped[group_key] = {
                "event_id": event_id,
                "source_category": source_category,
                "folder": folder,
                "timestamp": timestamp,
                "cameras": {},
                "camera_count": 0,
                "missing_common_cameras": [],
                "files": [],
                "event_json_path": norm_path(event_json_path) if event_json_path else None,
                "thumb_path": norm_path(thumb_path) if thumb_path else None,
                **read_event_json(event_json_path),
            }

        group = grouped[group_key]
        group["files"].append(norm_path(file_path))

        if camera:
            if camera in SUPPORTED_CAMERAS:
                group["cameras"][camera] = norm_path(file_path)
            else:
                unknown_cameras[camera] += 1
                group["cameras"][camera] = norm_path(file_path)

    event_groups = []
    for group in grouped.values():
        group["camera_count"] = len(group["cameras"])
        group["missing_common_cameras"] = [
            camera for camera in COMMON_CAMERAS if camera not in group["cameras"]
        ]
        event_groups.append(group)

    event_groups.sort(
        key=lambda group: (
            group["source_category"],
            group["folder"],
            group["timestamp"] or group["event_id"],
        )
    )
    return event_groups, unknown_cameras


def write_output(payload):
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2)
        file.write("\n")


def print_summary(payload):
    print("Mimir Footage Source Discovery")
    print("=============================")
    print(f"selected_input: {payload['selected_input']}")
    print(f"detected_source_type: {payload['detected_source_type']}")
    print(f"drive_root: {payload['drive_root']}")
    print(f"teslacam_root: {payload['teslacam_root']}")
    print("scan_roots:")
    for root in payload["scan_roots"]:
        print(f"  - {root}")
    print(f"total_mp4_files: {payload['total_mp4_files']}")
    print(f"total_event_groups: {payload['total_event_groups']}")
    print("counts_by_source_category:")
    for category, count in payload["counts_by_source_category"].items():
        print(f"  {category}: {count}")
    print(f"incomplete_event_groups: {payload['incomplete_event_groups']}")
    print("unknown_camera_suffixes:")
    if payload["unknown_camera_suffixes"]:
        for camera, count in payload["unknown_camera_suffixes"].items():
            print(f"  {camera}: {count}")
    else:
        print("  none")
    print(f"output_json: {payload['output_json']}")


def main():
    args = parse_args()
    selected = safe_resolve(Path(args.input))
    classification = classify_input(selected)
    scan_roots = classification["scan_roots"]
    mp4_files = discover_mp4_files(scan_roots) if scan_roots else []
    event_groups, unknown_cameras = build_event_groups(mp4_files)
    counts = Counter(group["source_category"] for group in event_groups)
    incomplete = sum(
        1
        for group in event_groups
        if group["source_category"] in TESLACAM_FOLDERS and group["missing_common_cameras"]
    )

    payload = {
        "selected_input": norm_path(selected),
        "detected_source_type": classification["detected_source_type"],
        "drive_root": norm_path(classification["drive_root"]) if classification["drive_root"] else None,
        "teslacam_root": norm_path(classification["teslacam_root"]) if classification["teslacam_root"] else None,
        "scan_roots": [norm_path(root) for root in scan_roots],
        "total_mp4_files": len(mp4_files),
        "total_event_groups": len(event_groups),
        "counts_by_source_category": dict(sorted(counts.items())),
        "incomplete_event_groups": incomplete,
        "unknown_camera_suffixes": dict(sorted(unknown_cameras.items())),
        "event_groups": event_groups,
        "output_json": norm_path(OUTPUT_PATH),
    }

    write_output(payload)
    print_summary(payload)


if __name__ == "__main__":
    main()
