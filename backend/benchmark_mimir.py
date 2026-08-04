import csv
import json
import os
import sys
from pathlib import Path


BASE = Path(r"C:\MimirDev\backend")
LABELS_CSV = BASE / "benchmark_labels.csv"
LATEST_SESSION_JSON = BASE / "MimirOutput" / "latest_session.json"

VALID_SEVERITIES = {"IMPORTANT", "REVIEW", "IGNORE"}
VALID_CATEGORIES = {
    "rear_impact",
    "door_ding",
    "side_contact",
    "person_near",
    "person_touching",
    "normal_traffic",
    "distant_pedestrian",
    "weird_unclear",
}
SEVERITY_PRIORITY = {
    "IGNORE": 0,
    "REVIEW": 1,
    "IMPORTANT": 2,
}


def normalize_text(value):
    return str(value or "").strip()


def normalize_key(value):
    return normalize_text(value).lower().replace("\\", "/")


def normalize_filename(value):
    text = normalize_text(value)

    if not text:
        return ""

    return os.path.basename(text.replace("\\", "/"))


def normalize_severity(value):
    severity = normalize_text(value).upper()
    return severity if severity in VALID_SEVERITIES else ""


def stronger_severity(left, right):
    left = normalize_severity(left) or "IGNORE"
    right = normalize_severity(right) or "IGNORE"

    return right if SEVERITY_PRIORITY[right] > SEVERITY_PRIORITY[left] else left


def create_label_template(path):
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "filename_or_group",
                "expected_severity",
                "category",
                "notes",
            ],
        )
        writer.writeheader()


def ensure_labels_csv(path):
    if path.exists():
        return

    create_label_template(path)


def label_instructions():
    categories = ", ".join(sorted(VALID_CATEGORIES))
    print("No benchmark labels found.")
    print(f"Add rows to: {LABELS_CSV}")
    print("Columns:")
    print("  filename_or_group,expected_severity,category,notes")
    print("Example:")
    print("  2026-03-03_14-31-54,IMPORTANT,rear_impact,rear crash should never be ignored")
    print(f"Categories: {categories}")


def read_labels(path):
    ensure_labels_csv(path)
    labels = []

    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        fieldnames = reader.fieldnames or []

        if not fieldnames:
            return labels

        missing = [
            field
            for field in [
                "filename_or_group",
                "expected_severity",
                "category",
                "notes",
            ]
            if field not in fieldnames
        ]

        if missing:
            raise ValueError(
                "benchmark_labels.csv is missing required columns: "
                + ", ".join(missing)
            )

        for row_number, row in enumerate(reader, start=2):
            label_key = normalize_text(row.get("filename_or_group"))
            expected = normalize_severity(row.get("expected_severity"))
            category = normalize_text(row.get("category")).lower()
            notes = normalize_text(row.get("notes"))

            if not label_key and not expected and not category and not notes:
                continue

            if not label_key:
                raise ValueError(f"Missing filename_or_group on row {row_number}")

            if not expected:
                raise ValueError(
                    f"Invalid expected_severity on row {row_number}: "
                    f"{row.get('expected_severity')}"
                )

            if category and category not in VALID_CATEGORIES:
                raise ValueError(
                    f"Invalid category on row {row_number}: {category}. "
                    f"Use one of: {', '.join(sorted(VALID_CATEGORIES))}"
                )

            labels.append(
                {
                    "filename_or_group": label_key,
                    "expected_severity": expected,
                    "category": category,
                    "notes": notes,
                }
            )

    return labels


def read_session(path):
    if not path.exists():
        raise FileNotFoundError(f"latest_session.json not found: {path}")

    with path.open("r", encoding="utf-8") as file:
        session = json.load(file)

    if not isinstance(session, dict):
        raise ValueError("latest_session.json must contain a JSON object")

    incidents = session.get("incidents", [])

    if not isinstance(incidents, list):
        raise ValueError("latest_session.json field 'incidents' must be a list")

    return session


def camera_clip_values(incident):
    raw = incident.get("camera_clips")

    if isinstance(raw, list):
        return [
            clip
            for clip in raw
            if isinstance(clip, dict)
        ]

    if isinstance(raw, dict):
        clips = []

        for camera, value in raw.items():
            if isinstance(value, str):
                clips.append(
                    {
                        "camera": camera,
                        "path": value,
                        "filename": normalize_filename(value),
                    }
                )
            elif isinstance(value, dict):
                clip = dict(value)
                clip.setdefault("camera", camera)
                clips.append(clip)

        return clips

    return []


def incident_match_values(incident):
    values = set()

    for key in [
        "id",
        "source_video",
        "source_clip",
        "video_path",
        "original_source_video",
        "library_video_path",
        "event_group_id",
        "event_timestamp",
        "source_event_timestamp",
        "tesla_event_timestamp",
    ]:
        value = normalize_text(incident.get(key))

        if value:
            values.add(value)
            filename = normalize_filename(value)

            if filename:
                values.add(filename)

    for clip in camera_clip_values(incident):
        for key in [
            "filename",
            "path",
            "video_path",
            "source_video",
            "source_clip",
            "original_source_video",
            "library_path",
            "trash_path",
        ]:
            value = normalize_text(clip.get(key))

            if value:
                values.add(value)
                filename = normalize_filename(value)

                if filename:
                    values.add(filename)

    return values


def value_matches_label(value, label):
    value_key = normalize_key(value)
    label_key = normalize_key(label)
    value_filename = normalize_key(normalize_filename(value))
    label_filename = normalize_key(normalize_filename(label))

    if not value_key or not label_key:
        return False

    if value_key == label_key or value_filename == label_key or value_filename == label_filename:
        return True

    if value_key.startswith(label_key) or value_filename.startswith(label_key):
        return True

    if label_key in value_key or label_filename and label_filename in value_key:
        return True

    return False


def match_incident(label_key, incidents):
    best = None
    best_score = -1

    for incident in incidents:
        if not isinstance(incident, dict):
            continue

        values = incident_match_values(incident)
        score = -1

        for value in values:
            if not value_matches_label(value, label_key):
                continue

            if normalize_key(value) == normalize_key(label_key):
                score = max(score, 4)
            elif normalize_key(normalize_filename(value)) == normalize_key(normalize_filename(label_key)):
                score = max(score, 3)
            else:
                score = max(score, 1)

        if score > best_score:
            best = incident
            best_score = score

    return best if best_score >= 0 else None


def actual_severity(incident):
    if not incident:
        return "MISSING"

    severity = (
        normalize_severity(incident.get("final_severity"))
        or normalize_severity(incident.get("severity"))
        or normalize_severity(incident.get("user_status"))
    )

    return severity or "MISSING"


def notes_allow_review_at_most(notes):
    text = normalize_text(notes).lower()
    return "review at most" in text or "at most review" in text


def is_person_near_never_important_case(label):
    return (
        normalize_text(label.get("category")).lower() == "person_near"
        and "never important" in normalize_text(label.get("notes")).lower()
    )


def compare_label(label, incidents):
    incident = match_incident(label["filename_or_group"], incidents)
    expected = label["expected_severity"]
    actual = actual_severity(incident)
    person_near_never_important = is_person_near_never_important_case(label)
    review_at_most_allowed = notes_allow_review_at_most(label.get("notes"))
    passed = expected == actual

    if (
        person_near_never_important
        and review_at_most_allowed
        and actual in {"REVIEW", "IGNORE"}
    ):
        passed = True

    false_ignore = expected == "IMPORTANT" and actual == "IGNORE"
    false_important = expected != "IMPORTANT" and actual == "IMPORTANT"
    person_near_false_important = person_near_never_important and actual == "IMPORTANT"

    return {
        "passed": passed,
        "critical": false_ignore or person_near_false_important,
        "false_ignore": false_ignore,
        "false_important": false_important,
        "person_near_false_important": person_near_false_important,
        "expected": expected,
        "actual": actual,
        "matched_incident_id": incident.get("id", "") if isinstance(incident, dict) else "",
        "filename_or_group": label["filename_or_group"],
        "category": label["category"],
        "notes": label["notes"],
    }


def print_result(result):
    status = "PASS" if result["passed"] else "FAIL"

    if result["critical"]:
        status = "FAIL CRITICAL"

    print(status)
    print(f"  label: {result['filename_or_group']}")
    print(f"  expected: {result['expected']}")
    print(f"  actual: {result['actual']}")
    print(f"  matched incident id: {result['matched_incident_id'] or 'not matched'}")
    print(f"  category: {result['category'] or 'not set'}")
    print(f"  notes: {result['notes']}")


def main():
    labels = read_labels(LABELS_CSV)

    if not labels:
        label_instructions()
        return 0

    session = read_session(LATEST_SESSION_JSON)
    incidents = session.get("incidents", [])
    results = [
        compare_label(label, incidents)
        for label in labels
    ]
    passed = sum(1 for result in results if result["passed"])
    failed = len(results) - passed
    false_ignores = sum(1 for result in results if result["false_ignore"])
    false_importants = sum(1 for result in results if result["false_important"])
    person_near_false_importants = sum(
        1 for result in results if result["person_near_false_important"]
    )
    critical_failures = sum(1 for result in results if result["critical"])

    print("Mimir Detection Benchmark")
    print("=========================")

    for result in results:
        print_result(result)

    print("Summary")
    print("=======")
    print(f"total labels: {len(results)}")
    print(f"passed: {passed}")
    print(f"failed: {failed}")
    print(f"false_ignores: {false_ignores}")
    print(f"false_importants: {false_importants}")
    print(f"person_near_false_importants: {person_near_false_importants}")

    if critical_failures:
        print(f"critical failures: {critical_failures}")
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
