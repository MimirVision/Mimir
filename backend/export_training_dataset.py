import argparse
import csv
import json
import os
import shutil
from pathlib import Path


IMAGE_EXPORTS = [
    ("start_frame_image", "before"),
    ("best_frame_image", "peak"),
    ("end_frame_image", "after"),
    ("hero_thumbnail", "hero"),
    ("contact_sheet", "contact_sheet"),
]

LABEL_COLUMNS = [
    "incident_id",
    "status",
    "label_reason",
    "possible_contact",
    "possible_impact",
    "normal_traffic",
    "notes",
    "label_source",
]


def read_json(path):
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8", newline="\n") as file:
        for row in rows:
            file.write(json.dumps(to_json_safe(row), ensure_ascii=False))
            file.write("\n")


def to_json_safe(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, dict):
        return {str(key): to_json_safe(item) for key, item in value.items()}

    if isinstance(value, (list, tuple)):
        return [to_json_safe(item) for item in value]

    return str(value)


def default_feedback_folder():
    home = os.environ.get("USERPROFILE") or os.environ.get("HOME")
    if not home:
        return None
    return Path(home) / "Documents" / "Mimir Feedback"


def safe_name(value):
    text = str(value or "").strip() or "incident"
    safe = []
    for char in text:
        if char.isalnum() or char in {"-", "_", "."}:
            safe.append(char)
        else:
            safe.append("_")
    cleaned = "".join(safe).strip("_")
    return (cleaned or "incident")[:120]


def clean_status(value):
    status = str(value or "").strip().upper()
    if status in {"IMPORTANT", "REVIEW", "IGNORE"}:
        return status
    return ""


def incident_id_for(incident, index):
    return str(
        incident.get("id")
        or incident.get("incident_id")
        or incident.get("event_id")
        or f"incident_{index + 1:04d}"
    )


def get_nested(mapping, *keys):
    current = mapping
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def first_present(*values):
    for value in values:
        if value not in (None, ""):
            return value
    return None


def load_feedback_index(feedback_folder):
    index = {}
    if not feedback_folder:
        return index

    root = Path(feedback_folder)
    if not root.exists() or not root.is_dir():
        return index

    for feedback_path in root.rglob("feedback.json"):
        try:
            feedback = read_json(feedback_path)
        except Exception:
            continue

        incident_id = str(feedback.get("incident_id") or "").strip()
        if not incident_id:
            continue

        current = index.get(incident_id)
        current_time = str(current.get("timestamp") or current.get("saved_at") or "") if current else ""
        next_time = str(feedback.get("timestamp") or feedback.get("saved_at") or "")

        if current is None or next_time >= current_time:
            index[incident_id] = feedback

    return index


def feedback_status(feedback):
    label = str(feedback.get("user_selected_feedback") or "").strip().lower()
    if label == "should be important":
        return "IMPORTANT"
    if label == "should be review":
        return "REVIEW"
    if label == "should be ignore":
        return "IGNORE"
    return ""


def derive_label(incident, feedback):
    user_status = clean_status(incident.get("user_status"))
    manual_override = bool(incident.get("manual_status_override"))
    if user_status and (manual_override or incident.get("user_status") is not None):
        return user_status, "manual_override" if manual_override else "user_status", "User review status"

    feedback_label = feedback_status(feedback) if feedback else ""
    if feedback_label:
        return feedback_label, "feedback", str(feedback.get("user_selected_feedback") or "feedback")

    final_severity = clean_status(
        first_present(
            incident.get("final_severity"),
            incident.get("severity"),
        )
    )
    return final_severity or "IGNORE", "model", "final_severity"


def available_cameras(incident):
    cameras = incident.get("available_cameras")
    if isinstance(cameras, list):
        return cameras

    clips = incident.get("camera_clips")
    if isinstance(clips, list):
        return [
            clip.get("camera")
            for clip in clips
            if isinstance(clip, dict) and clip.get("camera")
        ]

    if isinstance(clips, dict):
        return [camera for camera in clips.keys() if camera]

    return []


def normal_traffic_value(incident):
    ai_review = incident.get("ai_evidence_review")
    if isinstance(ai_review, dict):
        return bool(ai_review.get("normal_passing_traffic", False))

    debug = incident.get("classification_debug")
    if isinstance(debug, dict):
        return bool(debug.get("normal_passing_traffic_evidence", False))

    return False


def copy_image_if_present(incident, incident_id, images_dir, field, suffix, warnings):
    source = incident.get(field)
    if not source:
        return None

    source_path = Path(str(source))
    if not source_path.exists() or not source_path.is_file():
        warnings.append(f"missing image for {incident_id}: {field}={source}")
        return None

    destination = images_dir / f"{safe_name(incident_id)}_{suffix}.jpg"
    try:
        shutil.copy2(source_path, destination)
        return str(destination)
    except Exception as exc:
        warnings.append(f"could not copy image for {incident_id}: {field} ({exc})")
        return None


def incident_metadata(incident, incident_id, feedback, exported_images):
    debug = incident.get("classification_debug")
    if not isinstance(debug, dict):
        debug = {}

    ai_review = incident.get("ai_evidence_review")
    if not isinstance(ai_review, dict):
        ai_review = {}

    local_summary = incident.get("local_evidence_summary")
    if not isinstance(local_summary, dict):
        local_summary = {}

    status, label_source, label_reason = derive_label(incident, feedback)
    notes = ""
    user_feedback_label = ""
    if feedback:
        notes = str(feedback.get("notes") or "").strip()
        user_feedback_label = str(feedback.get("user_selected_feedback") or "").strip()

    return {
        "incident_id": incident_id,
        "event_group_id": first_present(
            incident.get("event_group_id"),
            incident.get("camera_group_id"),
            incident.get("tesla_event_group_id"),
        ),
        "source_video": first_present(
            incident.get("source_video"),
            incident.get("original_source_video"),
            incident.get("video_path"),
        ),
        "available_cameras": available_cameras(incident),
        "primary_camera": first_present(incident.get("primary_camera"), incident.get("camera")),
        "severity": incident.get("severity"),
        "user_status": incident.get("user_status"),
        "manual_status_override": bool(incident.get("manual_status_override", False)),
        "possible_contact": bool(incident.get("possible_contact", False)),
        "possible_impact": bool(incident.get("possible_impact", False)),
        "impact_level": incident.get("impact_level"),
        "contact_level": incident.get("contact_level"),
        "motion_score": first_present(
            incident.get("max_motion_score"),
            incident.get("motion_score"),
            local_summary.get("motion_score"),
        ),
        "impact_score": first_present(incident.get("impact_score"), local_summary.get("impact_score")),
        "contact_score": first_present(incident.get("contact_score"), local_summary.get("contact_score")),
        "ai_recommended_severity": first_present(
            debug.get("ai_recommended_severity"),
            ai_review.get("recommended_severity"),
            ai_review.get("severity"),
        ),
        "final_severity": first_present(incident.get("final_severity"), incident.get("severity")),
        "final_decision_source": first_present(
            incident.get("final_decision_source"),
            debug.get("final_decision_source"),
        ),
        "user_feedback_label": user_feedback_label,
        "notes": notes,
        "label_status": status,
        "label_source": label_source,
        "label_reason": label_reason,
        "normal_traffic": normal_traffic_value(incident),
        "exported_images": exported_images,
    }


def export_dataset(session_path, output_folder, feedback_folder=None):
    session = read_json(session_path)
    incidents = session.get("incidents", [])
    if not isinstance(incidents, list):
        incidents = []

    output = Path(output_folder)
    images_dir = output / "images"
    clips_dir = output / "clips"
    metadata_dir = output / "metadata"
    images_dir.mkdir(parents=True, exist_ok=True)
    clips_dir.mkdir(parents=True, exist_ok=True)
    metadata_dir.mkdir(parents=True, exist_ok=True)

    feedback_index = load_feedback_index(feedback_folder)
    metadata_rows = []
    label_rows = []
    warnings = []
    copied_images = 0
    user_labeled_incidents = 0

    for index, incident in enumerate(incidents):
        if not isinstance(incident, dict):
            warnings.append(f"skipped malformed incident at index {index}")
            continue

        incident_id = incident_id_for(incident, index)
        feedback = feedback_index.get(incident_id)
        exported_images = {}

        for field, suffix in IMAGE_EXPORTS:
            destination = copy_image_if_present(
                incident,
                incident_id,
                images_dir,
                field,
                suffix,
                warnings,
            )
            if destination:
                exported_images[suffix] = destination
                copied_images += 1

        metadata = incident_metadata(incident, incident_id, feedback, exported_images)
        metadata_rows.append(metadata)

        if metadata["label_source"] in {"manual_override", "user_status", "feedback"}:
            user_labeled_incidents += 1

        label_rows.append({
            "incident_id": incident_id,
            "status": metadata["label_status"],
            "label_reason": metadata["label_reason"],
            "possible_contact": metadata["possible_contact"],
            "possible_impact": metadata["possible_impact"],
            "normal_traffic": metadata["normal_traffic"],
            "notes": metadata["notes"],
            "label_source": metadata["label_source"],
        })

    incidents_jsonl = metadata_dir / "incidents.jsonl"
    labels_csv = metadata_dir / "labels.csv"
    summary_json = metadata_dir / "export_summary.json"

    write_jsonl(incidents_jsonl, metadata_rows)
    with open(labels_csv, "w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=LABEL_COLUMNS)
        writer.writeheader()
        writer.writerows(label_rows)

    summary = {
        "session": str(Path(session_path)),
        "output": str(output),
        "feedback_folder": str(feedback_folder) if feedback_folder else "",
        "incidents": len(metadata_rows),
        "user_labeled_incidents": user_labeled_incidents,
        "images_exported": copied_images,
        "labels_csv": str(labels_csv),
        "incidents_jsonl": str(incidents_jsonl),
        "warnings": warnings,
    }
    with open(summary_json, "w", encoding="utf-8") as file:
        json.dump(summary, file, indent=2)
        file.write("\n")

    return summary


def parse_args():
    parser = argparse.ArgumentParser(
        description="Export labeled Mimir incidents for future training."
    )
    parser.add_argument(
        "--session",
        required=True,
        help="Path to latest_session.json.",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Output folder for the training dataset.",
    )
    parser.add_argument(
        "--feedback",
        default=None,
        help="Optional Documents\\Mimir Feedback folder. Defaults to the current user's Documents folder if present.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    feedback_folder = Path(args.feedback) if args.feedback else default_feedback_folder()
    summary = export_dataset(
        Path(args.session),
        Path(args.output),
        feedback_folder=feedback_folder,
    )

    print("Mimir training dataset export")
    print("=============================")
    print(f"Output: {summary['output']}")
    print(f"Incidents: {summary['incidents']}")
    print(f"User-labeled incidents: {summary['user_labeled_incidents']}")
    print(f"Images exported: {summary['images_exported']}")
    print(f"labels.csv: {summary['labels_csv']}")
    if summary["warnings"]:
        print(f"Warnings: {len(summary['warnings'])}")
        for warning in summary["warnings"][:10]:
            print(f"- {warning}")


if __name__ == "__main__":
    main()
