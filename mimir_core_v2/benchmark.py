"""Benchmark checks for Mimir Core v2 output.

Run:
    python -m mimir_core_v2.benchmark
"""

from __future__ import annotations

import csv
import json
import sys
import argparse
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = Path(__file__).resolve().parent
SESSION_PATH = ROOT / "MimirOutputV2" / "latest_session.json"
LABELS_PATH = PACKAGE_ROOT / "benchmark_labels.csv"
REPORT_PATH = ROOT / "MimirOutputV2" / "benchmark_report.json"

VALID_SEVERITIES = {"IGNORE", "REVIEW", "IMPORTANT"}


def normalize_text(value: object) -> str:
    return str(value or "").strip()


def normalize_lookup(value: object) -> str:
    return normalize_text(value).lower().replace("\\", "/")


def normalize_severity(value: object) -> str:
    severity = normalize_text(value).upper()
    return severity if severity in VALID_SEVERITIES else "MISSING"


def read_session() -> dict:
    with SESSION_PATH.open("r", encoding="utf-8") as file:
        data = json.load(file)
    return data if isinstance(data, dict) else {}


def read_labels() -> list[dict]:
    with LABELS_PATH.open("r", encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def write_report(report: dict) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with REPORT_PATH.open("w", encoding="utf-8") as file:
        json.dump(report, file, indent=2)


def incident_reference_blob(incident: dict) -> str:
    values = [
        incident.get("event_group_id", ""),
        incident.get("event_timestamp", ""),
        Path(str(incident.get("video_path") or "")).name,
        incident.get("video_path", ""),
    ]

    camera_clips = incident.get("camera_clips")
    if isinstance(camera_clips, list):
        for clip in camera_clips:
            if not isinstance(clip, dict):
                continue
            values.append(clip.get("filename", ""))
            values.append(clip.get("path", ""))

    return normalize_lookup(" ".join(str(value) for value in values if value))


def find_match(label_key: str, incidents: list[dict]) -> dict | None:
    key = normalize_lookup(label_key)
    if not key:
        return None

    for incident in incidents:
        if key in incident_reference_blob(incident):
            return incident

    return None


def allows_review_at_most(notes: str) -> bool:
    text = normalize_text(notes).lower()
    return "review at most" in text or "at most review" in text or "never important" in text


def is_never_important_case(category: str, notes: str) -> bool:
    return normalize_text(category).lower() in {"person_passby", "person_near"} and "never important" in normalize_text(notes).lower()


def evaluate_label(label: dict, incidents: list[dict], require_all_labels: bool = False) -> dict:
    expected = normalize_severity(label.get("expected_severity"))
    category = normalize_text(label.get("category")).lower()
    notes = normalize_text(label.get("notes"))
    matched = find_match(normalize_text(label.get("filename_or_group")), incidents)
    actual = normalize_severity(matched.get("final_severity") or matched.get("severity")) if matched else "MISSING"

    passed = False
    critical = False
    skipped = False
    reason = ""

    if matched is None:
        reason = "not present in current scan"
        skipped = not require_all_labels
    elif category == "person_passby" and actual == "IMPORTANT":
        critical = True
        reason = "person_passby became IMPORTANT"
    elif category == "person_near" and "never important" in notes.lower() and actual == "IMPORTANT":
        critical = True
        reason = "person_near never-important case became IMPORTANT"
    elif expected == "IMPORTANT" and actual == "IGNORE":
        critical = True
        reason = "IMPORTANT label became IGNORE"
    elif expected == actual:
        passed = True
        reason = "matched expected severity"
    elif expected == "REVIEW" and allows_review_at_most(notes) and actual in {"IGNORE", "REVIEW"}:
        passed = True
        reason = "within review-at-most allowance"
    else:
        reason = "severity mismatch"

    return {
        "label": label,
        "matched": matched,
        "expected": expected,
        "actual": actual,
        "passed": passed,
        "critical": critical,
        "skipped": skipped,
        "reason": reason,
    }


def print_result(result: dict) -> None:
    label = result["label"]
    matched = result["matched"]
    if result["skipped"]:
        status = "SKIP"
    else:
        status = "PASS" if result["passed"] else "FAIL"
    print(status)
    print(f"  label: {label.get('filename_or_group')}")
    print(f"  expected: {result['expected']}")
    print(f"  actual: {result['actual']}")
    print(f"  matched incident id: {matched.get('id', 'not matched') if matched else 'not matched'}")
    print(f"  category: {label.get('category')}")
    print(f"  notes: {label.get('notes')}")
    print(f"  reason: {result['reason']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Mimir Core v2 benchmark checks.")
    parser.add_argument(
        "--require-all-labels",
        action="store_true",
        help="Treat unmatched benchmark labels as failures instead of skips.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    print("Mimir Core v2 Benchmark")
    print("=======================")

    if not SESSION_PATH.exists():
        print(f"latest_session.json not found: {SESSION_PATH}")
        return 2
    if not LABELS_PATH.exists():
        print(f"benchmark_labels.csv not found: {LABELS_PATH}")
        return 2

    session = read_session()
    incidents = session.get("incidents")
    if not isinstance(incidents, list):
        print("latest_session.json does not contain an incidents list")
        return 2

    labels = read_labels()
    if not labels:
        print("No benchmark labels found. Add rows to benchmark_labels.csv.")
        return 0

    results = [evaluate_label(label, incidents, require_all_labels=args.require_all_labels) for label in labels]
    for result in results:
        print_result(result)

    passed = sum(1 for result in results if result["passed"])
    skipped_unmatched = sum(1 for result in results if result["skipped"])
    failed = sum(1 for result in results if not result["passed"] and not result["skipped"])
    critical = sum(1 for result in results if result["critical"])
    matched_count = sum(1 for result in results if result["matched"] is not None)
    false_ignores = sum(1 for result in results if result["expected"] == "IMPORTANT" and result["actual"] == "IGNORE")
    false_importants = sum(1 for result in results if result["actual"] == "IMPORTANT" and result["expected"] != "IMPORTANT")

    report_results = []
    for result in results:
        label = result["label"]
        matched = result["matched"]
        report_results.append(
            {
                "filename_or_group": label.get("filename_or_group", ""),
                "expected_severity": result["expected"],
                "actual_severity": result["actual"],
                "category": label.get("category", ""),
                "matched_incident_id": matched.get("id", "") if matched else "",
                "notes": label.get("notes", ""),
                "passed": result["passed"],
                "critical": result["critical"],
                "skipped": result["skipped"],
                "reason": result["reason"],
            }
        )

    report = {
        "labels_loaded": len(labels),
        "labels_matched": matched_count,
        "skipped_unmatched": skipped_unmatched,
        "passed": passed,
        "failed": failed,
        "critical_failures": critical,
        "false_importants": false_importants,
        "false_ignores": false_ignores,
        "results": report_results,
    }
    write_report(report)

    print("Summary")
    print("=======")
    print(f"labels loaded: {len(labels)}")
    print(f"labels matched: {matched_count}")
    print(f"skipped_unmatched: {skipped_unmatched}")
    print(f"passed: {passed}")
    print(f"failed: {failed}")
    print(f"critical_failures: {critical}")
    print(f"false_importants: {false_importants}")
    print(f"false_ignores: {false_ignores}")
    print(f"report: {REPORT_PATH}")

    if matched_count == 0:
        print()
        print("No benchmark labels matched the current scan. This is not a detection failure. Scan a benchmark folder that contains the labeled clips.")

    return 2 if critical else 0


if __name__ == "__main__":
    sys.exit(main())
