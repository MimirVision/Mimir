import argparse
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path


DEFAULT_SESSION = r"C:\Mimir_Backend\MimirOutput\latest_session.json"
VALID_STATUSES = {"IGNORE", "REVIEW", "IMPORTANT"}
VIDEO_FIELDS = [
    "video_path",
    "library_video_path",
    "source_video",
    "original_source_video",
    "source_clip",
]


def now_iso():
    return datetime.now().replace(microsecond=0).isoformat()


def json_result(payload, exit_code=0):
    print(json.dumps(payload, indent=2))
    raise SystemExit(exit_code)


def load_session(path):
    try:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
    except FileNotFoundError:
        raise ValueError(f"Session file was not found: {path}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"Could not parse session JSON: {exc}")
    except OSError as exc:
        raise ValueError(f"Could not read session file: {exc}")

    if not isinstance(data, dict):
        raise ValueError("Session JSON must be an object.")

    incidents = data.get("incidents")
    if not isinstance(incidents, list):
        raise ValueError("Session JSON must contain an incidents list.")

    return data


def atomic_write_json(path, data):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    fd, temp_path = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=str(target.parent),
        text=True,
    )

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=2)
            file.write("\n")
        os.replace(temp_path, target)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def normalize_status(value):
    status = str(value or "").upper()
    if status not in VALID_STATUSES:
        raise ValueError("Status must be one of IGNORE, REVIEW, IMPORTANT.")
    return status


def incident_matches(incident, incident_id, index):
    wanted = str(incident_id)
    candidates = [
        incident.get("id"),
        incident.get("incident_id"),
        incident.get("event_id"),
        index,
        index + 1,
    ]

    return any(str(candidate) == wanted for candidate in candidates if candidate is not None)


def find_incident(session, incident_id):
    for index, incident in enumerate(session.get("incidents", [])):
        if isinstance(incident, dict) and incident_matches(incident, incident_id, index):
            return incident

    raise ValueError(f"Incident was not found: {incident_id}")


def severity_folder(status):
    normalized = normalize_status(status)
    return {
        "IMPORTANT": "Important",
        "REVIEW": "Review",
        "IGNORE": "Ignore",
    }[normalized]


def existing_file_path(value):
    if not value:
        return None

    path = Path(str(value))
    try:
        if path.exists() and path.is_file():
            return path
    except OSError:
        return None

    return None


def path_string(value):
    return str(value) if value else ""


def folder_string(value):
    if not value:
        return ""

    try:
        path = Path(str(value))
        if path.is_dir():
            return str(path)
        parent = path.parent
        return str(parent) if str(parent) != "." else ""
    except (OSError, TypeError, ValueError):
        return ""


def best_known_video_path(incident):
    for field in ("video_path", "library_video_path", "trash_video_path", "source_video", "original_source_video", "source_clip"):
        value = incident.get(field)
        if value:
            return str(value)
    return ""


def current_video_path(incident):
    if incident.get("user_deleted") and incident.get("trash_video_path"):
        return str(incident.get("trash_video_path"))

    return best_known_video_path(incident)


def update_current_location_fields(incident):
    current_path = current_video_path(incident)
    current_folder = folder_string(current_path)

    incident["current_folder"] = current_folder

    if "original_source_video" not in incident:
        original = incident.get("source_video") or incident.get("source_clip") or current_path
        if original:
            incident["original_source_video"] = str(original)

    if "library_video_path" not in incident:
        incident["library_video_path"] = ""

    if "trash_video_path" not in incident:
        incident["trash_video_path"] = ""

    if "moved_to_library" not in incident:
        incident["moved_to_library"] = False

    if "user_deleted" not in incident:
        incident["user_deleted"] = False

    if current_path and "video_path" not in incident:
        incident["video_path"] = current_path

    return current_path, current_folder


def best_video_path(incident):
    for field in VIDEO_FIELDS:
        path = existing_file_path(incident.get(field))
        if path:
            return field, path

    return None, None


def unique_destination(folder, filename):
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / filename

    if not destination.exists():
        return destination

    stem = destination.stem
    suffix = destination.suffix
    counter = 1

    while True:
        candidate = folder / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def copy_verify_remove(source, destination):
    if not source.exists() or not source.is_file():
        raise ValueError(f"Source video file was not found: {source}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)

    if not destination.exists() or destination.stat().st_size <= 0:
        raise ValueError("Copied file could not be verified. Original file was left untouched.")

    try:
        os.unlink(source)
    except OSError as exc:
        try:
            destination.unlink()
        except OSError:
            pass
        raise ValueError(f"Could not remove original after verified copy: {exc}")


def append_action_log(incident, action, details=None):
    log = incident.get("user_action_log")
    if not isinstance(log, list):
        log = []
        incident["user_action_log"] = log

    entry = {
        "action": action,
        "created_at": now_iso(),
    }

    if isinstance(details, dict):
        entry.update(details)

    log.append(entry)


def set_status(incident, status):
    selected_status = normalize_status(status)
    previous = str(incident.get("severity") or "")

    incident["previous_severity"] = previous
    incident["severity"] = selected_status

    if "final_severity" in incident:
        incident["final_severity"] = selected_status

    incident["manual_status_override"] = True
    incident["user_status"] = selected_status

    append_action_log(
        incident,
        "set_status",
        {
            "previous_severity": previous,
            "new_severity": selected_status,
        },
    )

    current_path, current_folder = update_current_location_fields(incident)

    return {
        "message": f"Status changed to {selected_status.title()}.",
        "status": selected_status,
        "current_video_path": current_path,
        "current_folder": current_folder,
    }


def move_to_library(incident, library_root):
    source_field, source = best_video_path(incident)
    if not source:
        raise ValueError("No existing video file was found for this incident.")

    status = normalize_status(incident.get("severity", "REVIEW"))
    destination_folder = Path(library_root) / "Manual Imports" / severity_folder(status)
    destination = unique_destination(destination_folder, source.name)

    if not incident.get("original_source_video"):
        incident["original_source_video"] = str(source)

    copy_verify_remove(source, destination)

    incident["library_video_path"] = str(destination)
    incident["video_path"] = str(destination)
    incident["video_exists"] = True
    incident["moved_to_library"] = True
    incident["user_deleted"] = False
    incident["trash_video_path"] = incident.get("trash_video_path", "")
    incident["storage_action_applied"] = "move_to_library"
    incident["current_folder"] = str(destination.parent)

    append_action_log(
        incident,
        "move_to_library",
        {
            "source_path": str(source),
            "library_video_path": str(destination),
            "severity": status,
        },
    )

    update_current_location_fields(incident)

    return {
        "message": "Clip moved to Mimir Library",
        "library_video_path": str(destination),
        "library_folder": str(destination.parent),
        "mimir_library_root": str(library_root),
    }


def move_to_trash(incident, library_root):
    source_field, source = best_video_path(incident)
    if not source:
        raise ValueError("No existing video file was found for this incident.")

    trash_folder = Path(library_root) / "_Mimir Trash"
    destination = unique_destination(trash_folder, source.name)

    if not incident.get("original_source_video"):
        incident["original_source_video"] = str(source)

    copy_verify_remove(source, destination)

    incident["user_deleted"] = True
    incident["deleted_at"] = now_iso()
    incident["trash_video_path"] = str(destination)
    incident["video_path"] = str(destination)
    incident["video_exists"] = destination.exists() and destination.is_file()
    incident["moved_to_library"] = False
    incident["storage_action_applied"] = "mimir_trash"
    incident["current_folder"] = str(destination.parent)

    if source_field == "video_path":
        incident[source_field] = str(destination)
    elif source_field == "library_video_path":
        incident["library_video_path"] = ""

    append_action_log(
        incident,
        "mimir_trash",
        {
            "source_path": str(source),
            "trash_video_path": str(destination),
        },
    )

    update_current_location_fields(incident)

    return {
        "message": "Clip moved to Mimir Trash",
        "trash_video_path": str(destination),
        "trash_folder": str(destination.parent),
        "mimir_library_root": str(library_root),
    }


def recalculate_counts(session):
    counts = {"IMPORTANT": 0, "REVIEW": 0, "IGNORE": 0}

    for incident in session.get("incidents", []):
        if not isinstance(incident, dict):
            continue
        severity = str(incident.get("severity") or "IGNORE").upper()
        if severity not in counts:
            severity = "IGNORE"
        counts[severity] += 1

    session["important"] = counts["IMPORTANT"]
    session["review"] = counts["REVIEW"]
    session["ignore"] = counts["IGNORE"]


def incident_json_path(incident):
    for field in (
        "contact_sheet",
        "hero_thumbnail",
        "thumbnail",
        "best_frame_image",
        "start_frame_image",
        "end_frame_image",
    ):
        value = incident.get(field)
        if value:
            return Path(str(value)).parent / "incident.json"

    return None


def update_incident_json(incident):
    path = incident_json_path(incident)
    if not path:
        return False

    try:
        atomic_write_json(path, incident)
        return True
    except OSError:
        return False


def parse_args():
    parser = argparse.ArgumentParser(
        description="Apply manual Mimir clip actions after a scan."
    )
    parser.add_argument("--session", default=DEFAULT_SESSION)
    parser.add_argument("--incident-id", required=True)
    parser.add_argument("--set-status", choices=sorted(VALID_STATUSES))
    parser.add_argument("--move-to-library", action="store_true")
    parser.add_argument("--delete", action="store_true")
    parser.add_argument("--library-root")
    return parser.parse_args()


def validate_action_count(args):
    action_count = sum(
        [
            bool(args.set_status),
            bool(args.move_to_library),
            bool(args.delete),
        ]
    )

    if action_count != 1:
        raise ValueError(
            "Choose exactly one action: --set-status, --move-to-library, or --delete."
        )


def main():
    try:
        args = parse_args()
        validate_action_count(args)

        session_path = Path(args.session)
        library_root = Path(args.library_root) if args.library_root else Path.home() / "Videos" / "Mimir Library"
        session = load_session(session_path)
        incident = find_incident(session, args.incident_id)

        if args.set_status:
            action = "set_status"
            action_payload = set_status(incident, args.set_status)
        elif args.move_to_library:
            action = "move_to_library"
            action_payload = move_to_library(incident, library_root)
        else:
            action = "mimir_trash"
            action_payload = move_to_trash(incident, library_root)

        update_current_location_fields(incident)

        recalculate_counts(session)
        atomic_write_json(session_path, session)
        update_incident_json(incident)

        response = {
            "ok": True,
            "action": action,
            "incident_id": str(incident.get("id") or args.incident_id),
            "message": action_payload.get("message", "Action completed."),
            "updated_session": str(session_path),
        }
        response.update(action_payload)

        json_result(response)
    except Exception as exc:
        json_result({"ok": False, "error": str(exc)}, exit_code=1)


if __name__ == "__main__":
    main()
