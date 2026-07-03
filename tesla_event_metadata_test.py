import json
import os
import re


BASE = r"C:\Mimir_Backend"
INPUT_FOLDER = os.path.join(BASE, "TestSet")
OUTPUT_PATH = os.path.join(
    BASE,
    "MimirOutput",
    "tesla_event_metadata_test.json"
)

EXPECTED_CAMERAS = [
    "front",
    "back",
    "left_repeater",
    "right_repeater",
]

CAMERA_ALIASES = {
    "front": "front",
    "back": "back",
    "rear": "back",
    "left_repeater": "left_repeater",
    "right_repeater": "right_repeater",
    "left_pillar": "left_repeater",
    "right_pillar": "right_repeater",
    "left": "left_repeater",
    "right": "right_repeater",
}

CLIP_PATTERN = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})-(?P<camera>.+)\.mp4$",
    re.IGNORECASE
)


def read_event_json(path):
    try:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
    except Exception:
        return {}

    if isinstance(data, dict):
        return data

    return {"value": data}


def find_field(data, *names):
    if not isinstance(data, dict):
        return None

    lowered = {
        str(key).lower(): value
        for key, value in data.items()
    }

    for name in names:
        value = lowered.get(name.lower())

        if value is not None:
            return value

    return None


def parse_clip_name(filename):
    match = CLIP_PATTERN.match(filename)

    if not match:
        return None

    camera_name = match.group("camera").lower()
    camera = CAMERA_ALIASES.get(camera_name)

    if not camera:
        return None

    return {
        "timestamp": match.group("timestamp"),
        "camera": camera,
    }


def group_clips_by_timestamp(folder):
    grouped = {}
    mp4_count = 0

    try:
        names = os.listdir(folder)
    except OSError:
        return grouped, mp4_count

    for name in names:
        path = os.path.join(folder, name)

        if not os.path.isfile(path):
            continue

        if not name.lower().endswith(".mp4"):
            continue

        mp4_count += 1
        parsed = parse_clip_name(name)

        if not parsed:
            continue

        timestamp = parsed["timestamp"]
        camera = parsed["camera"]

        grouped.setdefault(timestamp, {})[camera] = path

    return grouped, mp4_count


def timestamp_to_iso(value):
    if not value:
        return None

    text = str(value)
    match = re.match(
        r"^(?P<date>\d{4}-\d{2}-\d{2})_(?P<hour>\d{2})-(?P<minute>\d{2})-(?P<second>\d{2})$",
        text
    )

    if not match:
        return text.replace("_", "T")

    return (
        f"{match.group('date')}T"
        f"{match.group('hour')}:"
        f"{match.group('minute')}:"
        f"{match.group('second')}"
    )


def build_events_for_folder(folder, event_json_path):
    raw_event_json = read_event_json(event_json_path)
    grouped_clips, mp4_count = group_clips_by_timestamp(folder)
    events = []

    event_timestamp = find_field(
        raw_event_json,
        "timestamp",
        "event_timestamp",
        "eventTime",
        "event_time"
    )
    event_reason = find_field(
        raw_event_json,
        "reason",
        "event_reason",
        "trigger",
        "eventTrigger"
    )
    city = find_field(raw_event_json, "city")
    est_lat = find_field(raw_event_json, "est_lat", "estLat", "latitude", "lat")
    est_lon = find_field(raw_event_json, "est_lon", "estLon", "longitude", "lon", "lng")

    for timestamp in sorted(grouped_clips):
        cameras = grouped_clips[timestamp]
        missing_cameras = [
            camera
            for camera in EXPECTED_CAMERAS
            if camera not in cameras
        ]

        events.append(
            {
                "event_id": timestamp,
                "folder": folder,
                "event_json_path": event_json_path,
                "event_timestamp": event_timestamp or timestamp_to_iso(timestamp),
                "event_reason": event_reason,
                "city": city,
                "est_lat": est_lat,
                "est_lon": est_lon,
                "raw_event_json": raw_event_json,
                "cameras": cameras,
                "missing_cameras": missing_cameras,
                "camera_count": len(cameras),
            }
        )

    return events, mp4_count


def scan_test_set(input_folder):
    folders_scanned = 0
    event_json_files = []
    mp4_files_found = 0

    if not os.path.isdir(input_folder):
        return folders_scanned, event_json_files, mp4_files_found

    for root, _dirs, files in os.walk(input_folder):
        folders_scanned += 1

        for name in files:
            if name.lower() == "event.json":
                event_json_files.append(
                    os.path.join(root, name)
                )

            if name.lower().endswith(".mp4"):
                mp4_files_found += 1

    return folders_scanned, event_json_files, mp4_files_found


def write_output(data):
    os.makedirs(
        os.path.dirname(OUTPUT_PATH),
        exist_ok=True
    )

    with open(OUTPUT_PATH, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2)


def main():
    folders_scanned, event_json_files, mp4_files_found = scan_test_set(INPUT_FOLDER)
    events = []

    for event_json_path in event_json_files:
        folder = os.path.dirname(event_json_path)
        folder_events, _folder_mp4_count = build_events_for_folder(
            folder,
            event_json_path
        )

        events.extend(folder_events)

    incomplete_events = sum(
        1
        for event in events
        if event["missing_cameras"]
    )

    output = {
        "input_folder": INPUT_FOLDER,
        "folders_with_event_json": len(event_json_files),
        "events": events,
    }

    write_output(output)

    print("Tesla Event Metadata Test")
    print("=========================")
    print(f"Folders scanned: {folders_scanned}")
    print(f"event.json files found: {len(event_json_files)}")
    print(f"MP4 files found: {mp4_files_found}")
    print(f"TeslaCam events created: {len(events)}")
    print(f"Incomplete events: {incomplete_events}")
    print(f"Output path: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
