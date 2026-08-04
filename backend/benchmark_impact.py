import csv
import json
import os


BASE = r"C:\MimirDev\backend"
LABELS_CSV = os.path.join(BASE, "impact_labels.csv")
LATEST_SESSION_JSON = os.path.join(BASE, "MimirOutput", "latest_session.json")
VALID_IMPACT_LEVELS = {"NONE", "LOW", "MEDIUM", "HIGH"}
IMPACT_PRIORITY = {
    "NONE": 0,
    "LOW": 1,
    "MEDIUM": 2,
    "HIGH": 3,
}


def create_label_template(path):
    with open(path, "w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=["filename", "expected_impact", "expected_severity", "notes"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "filename": "example_clip.mp4",
                "expected_impact": "NONE",
                "expected_severity": "IGNORE",
                "notes": "Replace this row with a real labeled clip.",
            }
        )


def ensure_labels_csv(path):
    if os.path.exists(path):
        return True

    create_label_template(path)
    print("\nCreated starter impact_labels.csv")
    print("Fill it with real filenames and expected impact levels, then run this script again.")
    print(f"Path: {path}")
    return False


def normalize_filename(value):
    return os.path.basename(str(value or "").strip())


def filename_matches(label_filename, actual_filename):
    label = normalize_filename(label_filename).lower()
    actual = normalize_filename(actual_filename).lower()

    return (
        label == actual
        or actual.startswith(f"{label},")
        or actual.startswith(f"{label} ")
    )


def lookup_by_filename(mapping, filename):
    if filename in mapping:
        return mapping[filename]

    for actual_filename, value in mapping.items():
        if filename_matches(filename, actual_filename):
            return value

    return None


def contains_filename(filenames, filename):
    return any(
        filename_matches(filename, actual_filename)
        for actual_filename in filenames
    )


def normalize_impact_level(value):
    level = str(value or "").strip().upper()

    if level in VALID_IMPACT_LEVELS:
        return level

    return ""


def read_labels(path):
    labels = []

    with open(path, "r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)

        for row_number, row in enumerate(reader, start=2):
            filename = normalize_filename(row.get("filename"))
            expected = normalize_impact_level(row.get("expected_impact"))
            expected_severity = str(row.get("expected_severity", "")).strip().upper()
            notes = str(row.get("notes", "")).strip()

            if not filename:
                continue

            if not expected:
                raise ValueError(
                    f"Invalid expected_impact on row {row_number}: {row.get('expected_impact')}"
                )

            labels.append(
                {
                    "filename": filename,
                    "expected_impact": expected,
                    "expected_severity": expected_severity,
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

    return session


def find_video_sources(folder):
    sources = set()

    if not folder or not os.path.isdir(folder):
        return sources

    for _root, _dirs, files in os.walk(folder):
        for filename in files:
            if filename.lower().endswith(".mp4"):
                sources.add(normalize_filename(filename))

    return sources


def extract_processed_sources(session):
    processed = set()
    candidate_keys = [
        "processed_clips",
        "processed_files",
        "source_videos",
        "videos",
        "clips",
    ]

    for key in candidate_keys:
        values = session.get(key, [])

        if not isinstance(values, list):
            continue

        for value in values:
            if isinstance(value, dict):
                filename = (
                    value.get("source_video")
                    or value.get("filename")
                    or value.get("path")
                    or value.get("name")
                )
            else:
                filename = value

            filename = normalize_filename(filename)

            if filename:
                processed.add(filename)

    if session.get("safe_input_mode") is True:
        processed.update(find_video_sources(session.get("input_folder")))

    return processed


def stronger_impact(left, right):
    if IMPACT_PRIORITY[right] > IMPACT_PRIORITY[left]:
        return right

    return left


def build_actual_impact_map(incidents):
    actual = {}

    for incident in incidents:
        if not isinstance(incident, dict):
            continue

        source_video = normalize_filename(incident.get("source_video"))
        impact_level = normalize_impact_level(incident.get("impact_level"))

        if not impact_level:
            impact_level = "MEDIUM" if incident.get("possible_impact") is True else "NONE"

        if not source_video:
            continue

        current = actual.get(source_video)

        if current is None:
            actual[source_video] = impact_level
            continue

        actual[source_video] = stronger_impact(current, impact_level)

    return actual


def is_medium_or_high(level):
    return level in {"MEDIUM", "HIGH"}


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
    if not ensure_labels_csv(LABELS_CSV):
        return

    if not os.path.exists(LATEST_SESSION_JSON):
        raise FileNotFoundError(f"Latest session JSON not found: {LATEST_SESSION_JSON}")

    labels = read_labels(LABELS_CSV)
    session = read_session(LATEST_SESSION_JSON)
    actual_by_video = build_actual_impact_map(session["incidents"])
    processed_sources = extract_processed_sources(session)

    mismatches = []
    exact_matches = 0
    false_negatives = []
    false_positives = []
    expected_positive_count = 0
    recalled_positive_count = 0

    for label in labels:
        filename = label["filename"]
        expected = label["expected_impact"]
        actual = lookup_by_filename(actual_by_video, filename)

        if actual is None and contains_filename(processed_sources, filename):
            actual = "NONE"

        if actual is None:
            actual = "MISSING"

        if actual == expected:
            exact_matches += 1
        else:
            mismatches.append(
                {
                    "filename": filename,
                    "expected_impact": expected,
                    "actual_impact": actual,
                    "notes": label["notes"],
                }
            )

        expected_positive = is_medium_or_high(expected)
        actual_positive = is_medium_or_high(actual)

        if expected_positive:
            expected_positive_count += 1

            if actual_positive:
                recalled_positive_count += 1
            else:
                false_negatives.append(
                    {
                        "filename": filename,
                        "expected_impact": expected,
                        "actual_impact": actual,
                        "notes": label["notes"],
                    }
                )

        if actual_positive and not expected_positive:
            false_positives.append(
                {
                    "filename": filename,
                    "expected_impact": expected,
                    "actual_impact": actual,
                    "notes": label["notes"],
                }
            )

    total = len(labels)
    recall = (
        recalled_positive_count / expected_positive_count * 100
        if expected_positive_count
        else 0.0
    )

    print("\nMimir Impact Benchmark Summary")
    print("==============================")
    summary_widths = [32, 14]
    print_row(["Metric", "Value"], summary_widths)
    print_rule(summary_widths)
    print_row(["Total clips", total], summary_widths)
    print_row(["Exact matches", exact_matches], summary_widths)
    print_row(["Impact recall MEDIUM/HIGH", f"{recall:.1f}%"], summary_widths)
    print_row(["False negatives", len(false_negatives)], summary_widths)
    print_row(["False positives", len(false_positives)], summary_widths)

    print("\nMismatches")
    print("==========")

    if not mismatches:
        print("No mismatches.")
        return

    widths = [32, 16, 14, 40]
    print_row(["filename", "expected_impact", "actual_impact", "notes"], widths)
    print_rule(widths)

    for mismatch in mismatches:
        print_row(
            [
                mismatch["filename"],
                mismatch["expected_impact"],
                mismatch["actual_impact"],
                mismatch["notes"],
            ],
            widths,
        )


if __name__ == "__main__":
    main()
