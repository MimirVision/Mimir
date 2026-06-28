import csv
import json
import os


BASE = r"C:\Mimir_Backend"
LABELS_CSV = os.path.join(BASE, "benchmark_labels.csv")
LATEST_SESSION_JSON = os.path.join(BASE, "MimirOutput", "latest_session.json")
VALID_LABELS = {"IMPORTANT", "REVIEW", "IGNORE"}


def normalize_label(value):
    label = str(value or "").strip().upper()

    if label in VALID_LABELS:
        return label

    return ""


def read_labels(path):
    labels = []

    with open(path, "r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)

        for row_number, row in enumerate(reader, start=2):
            filename = str(row.get("filename", "")).strip()
            expected = normalize_label(row.get("expected"))
            notes = str(row.get("notes", "")).strip()

            if not filename:
                continue

            if not expected:
                raise ValueError(
                    f"Invalid expected label on row {row_number}: {row.get('expected')}"
                )

            labels.append(
                {
                    "filename": filename,
                    "expected": expected,
                    "notes": notes,
                }
            )

    return labels


def read_session(path):
    with open(path, "r", encoding="utf-8") as file:
        session = json.load(file)

    if not isinstance(session, dict):
        raise ValueError("latest_session.json must contain a JSON object")

    incidents = session.get("incidents", [])

    if not isinstance(incidents, list):
        raise ValueError("latest_session.json incidents must be a list")

    return incidents


def build_actual_map(incidents):
    actual = {}

    for incident in incidents:
        if not isinstance(incident, dict):
            continue

        source_video = str(incident.get("source_video", "")).strip()
        severity = normalize_label(incident.get("severity"))

        if not source_video or not severity:
            continue

        current = actual.get(source_video)

        if current is None:
            actual[source_video] = severity
            continue

        actual[source_video] = stronger_label(current, severity)

    return actual


def stronger_label(left, right):
    priority = {
        "IGNORE": 0,
        "REVIEW": 1,
        "IMPORTANT": 2,
    }

    if priority[right] > priority[left]:
        return right

    return left


def print_row(columns, widths):
    print(
        " | ".join(
            str(value).ljust(width)
            for value, width in zip(columns, widths)
        )
    )


def print_rule(widths):
    print(
        "-+-".join(
            "-" * width
            for width in widths
        )
    )


def main():
    if not os.path.exists(LABELS_CSV):
        raise FileNotFoundError(f"Labels CSV not found: {LABELS_CSV}")

    if not os.path.exists(LATEST_SESSION_JSON):
        raise FileNotFoundError(f"Latest session JSON not found: {LATEST_SESSION_JSON}")

    labels = read_labels(LABELS_CSV)
    incidents = read_session(LATEST_SESSION_JSON)
    actual_by_video = build_actual_map(incidents)

    mismatches = []
    matched = 0

    for label in labels:
        filename = label["filename"]
        expected = label["expected"]
        actual = actual_by_video.get(filename)

        if actual is None:
            actual = "MISSING"

        if actual == expected:
            matched += 1
        else:
            mismatches.append(
                {
                    "filename": filename,
                    "expected": expected,
                    "actual": actual,
                    "notes": label["notes"],
                }
            )

    total = len(labels)
    mismatch_count = len(mismatches)
    accuracy = (matched / total * 100) if total else 0.0

    print("\nMimir Benchmark Summary")
    print("=======================")
    print(f"Total labeled clips : {total}")
    print(f"Matched count       : {matched}")
    print(f"Mismatch count      : {mismatch_count}")
    print(f"Accuracy            : {accuracy:.1f}%")

    print("\nMismatches")
    print("==========")

    if not mismatches:
        print("No mismatches.")
        return

    widths = [32, 10, 10, 40]
    print_row(["filename", "expected", "actual", "notes"], widths)
    print_rule(widths)

    for mismatch in mismatches:
        print_row(
            [
                mismatch["filename"],
                mismatch["expected"],
                mismatch["actual"],
                mismatch["notes"],
            ],
            widths,
        )


if __name__ == "__main__":
    main()
