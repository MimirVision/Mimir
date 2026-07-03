import csv
import json
import os
import sys


BASE = r"C:\Mimir_Backend"
SESSION_PATH = os.path.join(BASE, "MimirOutput", "latest_session.json")
BENCHMARK_LABELS_CSV = os.path.join(BASE, "benchmark_labels.csv")
IMPACT_LABELS_CSV = os.path.join(BASE, "impact_labels.csv")

CRITICAL_TERMS = [
    "rear-end",
    "crash",
    "impact",
    "door ding",
    "contact",
    "must never be ignored",
]

SEVERITY_PRIORITY = {
    "IGNORE": 0,
    "REVIEW": 1,
    "IMPORTANT": 2,
}

IMPACT_PRIORITY = {
    "NONE": 0,
    "LOW": 1,
    "MEDIUM": 2,
    "HIGH": 3,
}


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


def normalize_severity(value):
    severity = str(value or "").strip().upper()

    if severity in SEVERITY_PRIORITY:
        return severity

    return ""


def normalize_impact(value):
    impact = str(value or "").strip().upper()

    if impact in IMPACT_PRIORITY:
        return impact

    return ""


def is_critical(notes):
    text = str(notes or "").lower()

    return any(term in text for term in CRITICAL_TERMS)


def stronger_severity(left, right):
    if SEVERITY_PRIORITY[right] > SEVERITY_PRIORITY[left]:
        return right

    return left


def stronger_impact(left, right):
    if IMPACT_PRIORITY[right] > IMPACT_PRIORITY[left]:
        return right

    return left


def load_session():
    with open(SESSION_PATH, "r", encoding="utf-8") as file:
        session = json.load(file)

    if not isinstance(session, dict):
        raise ValueError("latest_session.json must contain a JSON object")

    incidents = session.get("incidents", [])

    if not isinstance(incidents, list):
        raise ValueError("latest_session.json incidents must be a list")

    return session


def build_actual_maps(session):
    severities = {}
    impacts = {}

    for incident in session.get("incidents", []):
        if not isinstance(incident, dict):
            continue

        source_video = normalize_filename(incident.get("source_video"))

        if not source_video:
            continue

        severity = (
            normalize_severity(incident.get("final_severity"))
            or normalize_severity(incident.get("severity"))
            or normalize_severity(incident.get("ai_decision"))
        )

        impact = normalize_impact(incident.get("impact_level"))

        if not impact:
            impact = "MEDIUM" if incident.get("possible_impact") is True else "NONE"

        if severity:
            current_severity = severities.get(source_video)
            severities[source_video] = (
                severity
                if current_severity is None
                else stronger_severity(current_severity, severity)
            )

        current_impact = impacts.get(source_video)
        impacts[source_video] = (
            impact
            if current_impact is None
            else stronger_impact(current_impact, impact)
        )

    return severities, impacts


def lookup(mapping, filename):
    for actual_filename, value in mapping.items():
        if filename_matches(filename, actual_filename):
            return value

    return None


def read_csv_rows(path):
    if not os.path.exists(path):
        return []

    with open(path, "r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def check_benchmark_labels(actual_severities):
    failures = 0
    checked = 0

    for row in read_csv_rows(BENCHMARK_LABELS_CSV):
        filename = normalize_filename(row.get("filename"))
        expected = normalize_severity(row.get("expected"))
        notes = row.get("notes", "")

        if not filename or not is_critical(notes):
            continue

        checked += 1
        actual = lookup(actual_severities, filename) or "MISSING"

        if expected == "IMPORTANT" and actual in {"IGNORE", "MISSING"}:
            print(f"FAIL {filename} expected IMPORTANT actual {actual}")
            failures += 1
        else:
            expected_text = expected or "UNSPECIFIED"
            print(f"PASS {filename} expected {expected_text} actual {actual}")

    return checked, failures


def check_impact_labels(actual_severities, actual_impacts):
    failures = 0
    checked = 0

    for row in read_csv_rows(IMPACT_LABELS_CSV):
        filename = normalize_filename(row.get("filename"))
        expected_impact = normalize_impact(row.get("expected_impact"))
        notes = row.get("notes", "")

        if not filename or not is_critical(notes):
            continue

        checked += 1
        actual_impact = lookup(actual_impacts, filename) or "MISSING"
        actual_severity = lookup(actual_severities, filename) or "MISSING"

        if expected_impact == "HIGH":
            passed = (
                actual_impact == "HIGH"
                or actual_severity == "IMPORTANT"
            )
        else:
            passed = True

        if passed:
            print(
                f"PASS {filename} expected_impact {expected_impact} "
                f"actual_impact {actual_impact} final_severity {actual_severity}"
            )
        else:
            print(
                f"FAIL {filename} expected_impact {expected_impact} "
                f"actual_impact {actual_impact} final_severity {actual_severity}"
            )
            failures += 1

    return checked, failures


def main():
    if not os.path.exists(SESSION_PATH):
        print(f"FAIL latest_session.json missing: {SESSION_PATH}")
        sys.exit(1)

    session = load_session()
    actual_severities, actual_impacts = build_actual_maps(session)

    print("Mimir Critical Regression Check")
    print("===============================")

    severity_checked, severity_failures = check_benchmark_labels(
        actual_severities
    )
    impact_checked, impact_failures = check_impact_labels(
        actual_severities,
        actual_impacts
    )

    checked = severity_checked + impact_checked
    failures = severity_failures + impact_failures

    print()
    print(f"Critical rows checked: {checked}")
    print(f"Failures: {failures}")

    if failures:
        print("REGRESSION CHECK FAILED")
        sys.exit(1)

    print("REGRESSION CHECK PASSED")


if __name__ == "__main__":
    main()
