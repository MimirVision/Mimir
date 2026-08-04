import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "MimirOutputV2"
SESSION_PATH = OUTPUT_DIR / "latest_session.json"
REPORT_PATH = OUTPUT_DIR / "grouping_report.json"

TESLA_TIMESTAMP_RE = re.compile(
    r"(?P<timestamp>\d{4}-\d{2}-\d{2}[_-]\d{2}-\d{2}-\d{2})"
)
TESLA_CAMERA_RE = re.compile(
    r"-(front|back|left_repeater|right_repeater|left_pillar|right_pillar)(?:\.[^.]+)?$",
    re.IGNORECASE,
)


def safe_text(value, fallback=""):
    if value is None:
        return fallback
    try:
        text = str(value).strip()
    except Exception:
        return fallback
    return text or fallback


def path_parent(value):
    text = safe_text(value)
    if not text:
        return ""
    normalized = text.replace("/", "\\")
    index = normalized.rfind("\\")
    if index <= 0:
        return ""
    return normalized[:index]


def filename(value):
    text = safe_text(value)
    if not text:
        return ""
    normalized = text.replace("/", "\\")
    return normalized.rsplit("\\", 1)[-1]


def timestamp_prefix(value):
    text = safe_text(value)
    if not text:
        return ""
    match = TESLA_TIMESTAMP_RE.search(text)
    if not match:
        return ""
    return match.group("timestamp").replace("-", "_", 2)


def camera_from_text(value):
    name = filename(value)
    match = TESLA_CAMERA_RE.search(name)
    if match:
        return match.group(1).lower()
    return ""


def normalize_camera_clips(raw):
    clips = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                clips.append(item)
    elif isinstance(raw, dict):
        for key, value in raw.items():
            if isinstance(value, str):
                clips.append({
                    "camera": key,
                    "path": value,
                    "filename": filename(value),
                })
            elif isinstance(value, dict):
                clip = dict(value)
                clip.setdefault("camera", key)
                clips.append(clip)
    return clips


def incident_id(incident, index):
    return safe_text(
        incident.get("id")
        or incident.get("incident_id")
        or incident.get("event_id"),
        f"incident_index_{index}",
    )


def incident_source_video(incident):
    for key in [
        "source_video",
        "video_path",
        "library_video_path",
        "original_source_video",
        "source_clip",
    ]:
        value = safe_text(incident.get(key))
        if value:
            return value
    clips = normalize_camera_clips(incident.get("camera_clips"))
    for clip in clips:
        value = safe_text(
            clip.get("path")
            or clip.get("library_path")
            or clip.get("video_path")
            or clip.get("source_video")
            or clip.get("source_clip")
        )
        if value:
            return value
    return ""


def incident_timestamp(incident):
    for key in [
        "event_timestamp",
        "tesla_event_timestamp",
        "source_event_timestamp",
        "created_at",
        "event_group_id",
    ]:
        value = timestamp_prefix(incident.get(key))
        if value:
            return value
    return timestamp_prefix(incident_source_video(incident))


def incident_source_folder(incident):
    return (
        safe_text(incident.get("event_folder"))
        or path_parent(incident_source_video(incident))
    )


def incident_cameras(incident):
    cameras = []
    available = incident.get("available_cameras")
    if isinstance(available, list):
        cameras.extend(safe_text(camera) for camera in available if safe_text(camera))

    for clip in normalize_camera_clips(incident.get("camera_clips")):
        camera = safe_text(clip.get("camera")) or camera_from_text(
            clip.get("filename") or clip.get("path")
        )
        if camera:
            cameras.append(camera)

    source_camera = camera_from_text(incident_source_video(incident))
    if source_camera:
        cameras.append(source_camera)

    seen = set()
    unique = []
    for camera in cameras:
        key = camera.lower()
        if key not in seen:
            seen.add(key)
            unique.append(camera)
    return unique


def duplicate_keys_for_incident(incident):
    source_video = incident_source_video(incident)
    source_folder = incident_source_folder(incident)
    timestamp = incident_timestamp(incident)
    source_category = safe_text(incident.get("source_category"))
    event_folder = safe_text(incident.get("event_folder"))
    keys = []

    if event_folder:
        keys.append(("event_folder", event_folder))
    if timestamp:
        keys.append(("timestamp_prefix", timestamp))
    if source_folder:
        keys.append(("source_folder", source_folder))
    if source_category and timestamp:
        keys.append(("source_category_timestamp", f"{source_category}|{timestamp}"))
    if source_category and event_folder:
        keys.append(("source_category_event_folder", f"{source_category}|{event_folder}"))
    if source_video:
        parent = path_parent(source_video)
        stamp = timestamp_prefix(source_video)
        if parent and stamp:
            keys.append(("source_folder_timestamp", f"{parent}|{stamp}"))

    return keys


def summarize_incident(incident, index):
    clips = normalize_camera_clips(incident.get("camera_clips"))
    source_video = incident_source_video(incident)
    timestamp = incident_timestamp(incident)
    source_folder = incident_source_folder(incident)
    cameras = incident_cameras(incident)

    return {
        "id": incident_id(incident, index),
        "event_group_id": safe_text(incident.get("event_group_id")),
        "event_timestamp": safe_text(incident.get("event_timestamp")) or timestamp,
        "event_folder": safe_text(incident.get("event_folder")),
        "source_folder": source_folder,
        "source_category": safe_text(incident.get("source_category")),
        "source_video": source_video,
        "source_filename": filename(source_video),
        "severity": safe_text(
            incident.get("severity")
            or incident.get("final_severity")
            or incident.get("user_status"),
            "UNKNOWN",
        ),
        "camera_count": len(cameras),
        "cameras": cameras,
        "has_event_group_id": bool(safe_text(incident.get("event_group_id"))),
        "has_camera_clips": len(clips) > 0,
        "camera_clips_count": len(clips),
    }


def group_by(items, key_name):
    groups = defaultdict(list)
    for item in items:
        key = safe_text(item.get(key_name))
        if key:
            groups[key].append(item)
    return dict(groups)


def build_duplicate_groups(incidents):
    keyed = defaultdict(list)
    for item in incidents:
        original = item["_raw"]
        for key_type, key_value in duplicate_keys_for_incident(original):
            keyed[(key_type, key_value)].append(item)

    duplicate_groups = []
    seen_signatures = set()

    for (key_type, key_value), items in keyed.items():
        if len(items) < 2:
            continue

        ids = tuple(sorted(item["id"] for item in items))
        signature = (key_type, key_value, ids)
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)

        cameras = []
        source_videos = []
        severities = []
        for item in items:
            cameras.extend(item.get("cameras", []))
            if item.get("source_video"):
                source_videos.append(item["source_video"])
            severities.append(item.get("severity", "UNKNOWN"))

        duplicate_groups.append({
            "key_type": key_type,
            "group_key": key_value,
            "incident_count": len(items),
            "incident_ids": [item["id"] for item in items],
            "cameras_represented": sorted(set(cameras)),
            "source_videos": source_videos,
            "severities": sorted(set(severities)),
            "looks_like_split_camera_angles": (
                key_type in {
                    "timestamp_prefix",
                    "source_category_timestamp",
                    "source_folder_timestamp",
                }
                and len(set(cameras)) > 1
            ),
        })

    duplicate_groups.sort(
        key=lambda group: (
            -group["incident_count"],
            group["key_type"],
            group["group_key"],
        )
    )
    return duplicate_groups


def load_session():
    if not SESSION_PATH.exists():
        return {}, [f"latest_session.json not found: {SESSION_PATH}"]

    try:
        return json.loads(SESSION_PATH.read_text(encoding="utf-8")), []
    except Exception as exc:
        return {}, [f"Could not read latest_session.json: {exc}"]


def write_report(report):
    try:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(
            json.dumps(report, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return ""
    except Exception as exc:
        return f"Could not write grouping_report.json: {exc}"


def print_duplicate_group(group):
    print("")
    print(f"- {group['key_type']}: {group['group_key']}")
    print(f"  incidents: {group['incident_count']} ({', '.join(group['incident_ids'])})")
    print(f"  cameras: {', '.join(group['cameras_represented']) or 'unknown'}")
    print(f"  severities: {', '.join(group['severities']) or 'UNKNOWN'}")
    print("  source videos:")
    for path in group["source_videos"]:
        print(f"    - {path}")


def main():
    session, warnings = load_session()
    raw_incidents = session.get("incidents", [])
    if not isinstance(raw_incidents, list):
        warnings.append("latest_session.json field 'incidents' is not a list.")
        raw_incidents = []

    incidents = []
    for index, incident in enumerate(raw_incidents, start=1):
        if not isinstance(incident, dict):
            warnings.append(f"Incident at index {index} is not an object; skipped.")
            continue
        summary = summarize_incident(incident, index)
        summary["_raw"] = incident
        incidents.append(summary)

    incidents_with_event_group_id = [
        item for item in incidents if item["has_event_group_id"]
    ]
    incidents_with_camera_clips = [
        item for item in incidents if item["has_camera_clips"]
    ]
    timestamp_groups = {
        key: [item["id"] for item in values]
        for key, values in group_by(incidents, "event_timestamp").items()
    }
    duplicate_groups = build_duplicate_groups(incidents)

    grouped_incident_ids = {
        item["id"]
        for item in incidents
        if item["has_event_group_id"] and item["has_camera_clips"]
    }

    split_camera_duplicate_ids = {
        incident_id
        for group in duplicate_groups
        if group.get("looks_like_split_camera_angles")
        for incident_id in group["incident_ids"]
    }
    grouping_broken = bool(split_camera_duplicate_ids) and not split_camera_duplicate_ids.issubset(grouped_incident_ids)
    verdict = (
        "GROUPING BROKEN - camera angles are separate incidents"
        if grouping_broken
        else "GROUPING OK"
    )

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "session_path": str(SESSION_PATH),
        "total_incidents": len(incidents),
        "incidents_with_event_group_id": len(incidents_with_event_group_id),
        "incidents_with_camera_clips": len(incidents_with_camera_clips),
        "timestamp_groups": timestamp_groups,
        "possible_duplicate_groups": duplicate_groups,
        "verdict": verdict,
        "warnings": warnings,
        "incidents": [
            {key: value for key, value in item.items() if key != "_raw"}
            for item in incidents
        ],
    }

    write_warning = write_report(report)
    if write_warning:
        warnings.append(write_warning)
        report["warnings"] = warnings

    print("Mimir Grouping Inspection")
    print("=========================")
    print(f"Session: {SESSION_PATH}")
    print(f"Total incidents: {len(incidents)}")
    print(f"Incidents with event_group_id: {len(incidents_with_event_group_id)}")
    print(f"Incidents with camera_clips: {len(incidents_with_camera_clips)}")

    print("")
    print("Incidents grouped by event timestamp:")
    if timestamp_groups:
        for key, ids in sorted(timestamp_groups.items()):
            print(f"- {key}: {len(ids)} incident(s) ({', '.join(ids)})")
    else:
        print("- No event timestamps found.")

    print("")
    print("Possible duplicate incidents:")
    if duplicate_groups:
        for group in duplicate_groups:
            print_duplicate_group(group)
    else:
        print("- None detected.")

    if warnings:
        print("")
        print("Warnings:")
        for warning in warnings:
            print(f"- {warning}")

    print("")
    print(f"Verdict: {verdict}")
    print(f"Report written: {REPORT_PATH}")


if __name__ == "__main__":
    main()
