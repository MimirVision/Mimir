"""Diagnostic audit for Mimir Core v2 detection output.

This script only reads latest_session.json and writes audit reports. It does not
change scanner behavior, frontend behavior, or source footage.
"""

from __future__ import annotations

import csv
import json
import os
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "MimirOutputV2"
SESSION_PATH = OUTPUT_DIR / "latest_session.json"
JSON_REPORT_PATH = OUTPUT_DIR / "detection_audit_report.json"
CSV_REPORT_PATH = OUTPUT_DIR / "detection_audit_report.csv"

HIGH_MOTION_SCORE_THRESHOLD = 0.5
IMPORTANT_LEVELS = {"MEDIUM", "HIGH"}
TEXT_SUSPICION_PATTERNS = [
    "crash",
    "impact",
    "rear-ended",
    "rear ended",
    "door ding",
    "hit",
    "contact",
]

LOCAL_EVIDENCE_FIELDS = [
    "motion_score",
    "max_motion_score",
    "motion_spike_time_sec",
    "motion_spike_ratio",
    "camera_shake_score",
    "abrupt_scene_change",
    "strong_impact_like_motion",
    "possible_impact",
    "impact_level",
    "possible_contact",
    "contact_level",
    "vehicle_detected",
    "person_detected",
    "normal_traffic",
]

CLASSIFICATION_DEBUG_FIELDS = [
    "important_evidence_found",
    "important_evidence_reasons",
    "severity_cap_applied",
    "severity_cap_reason",
    "severity_floor_applied",
    "severity_floor_reason",
]

CSV_FIELDS = [
    "id",
    "event_timestamp",
    "final_severity",
    "event_type",
    "summary",
    "camera_count",
    "video_filename",
    *LOCAL_EVIDENCE_FIELDS,
    *CLASSIFICATION_DEBUG_FIELDS,
    "suspicious_ignored",
    "suspicious_reasons",
    "missing_thumbnail",
    "missing_local_evidence",
]


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    return data if isinstance(data, dict) else {}


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def compact_value(value: Any) -> Any:
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


def filename_from_path(path_value: Any) -> str:
    path_text = clean_text(path_value).strip()
    if not path_text:
        return ""
    return os.path.basename(path_text.rstrip("\\/"))


def incident_video_filename(incident: dict[str, Any]) -> str:
    filename = filename_from_path(incident.get("video_path"))
    if filename:
        return filename

    clips = as_list(incident.get("camera_clips"))
    for clip in clips:
        clip_data = as_dict(clip)
        filename = clean_text(clip_data.get("filename")).strip()
        if filename:
            return filename
        filename = filename_from_path(clip_data.get("path"))
        if filename:
            return filename
    return ""


def evidence_value(evidence: dict[str, Any], field: str) -> Any:
    if field == "abrupt_scene_change":
        return evidence.get("abrupt_scene_change", evidence.get("scene_change_score", ""))
    if field == "camera_shake_score":
        return evidence.get("camera_shake_score", evidence.get("shake_score", ""))
    return evidence.get(field, "")


def number_value(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def is_negated_text_hit(text: str, start_index: int) -> bool:
    prefix = text[max(0, start_index - 48) : start_index]
    return bool(
        re.search(
            r"\b(?:no|not|without|no clear|no visible|no likely)\b[\w\s,;:/-]{0,44}$",
            prefix,
        )
    )


def suspicious_text_reasons(filename: Any, summary: Any) -> list[str]:
    matches: list[str] = []
    filename_text = clean_text(filename).lower()
    summary_text = clean_text(summary).lower()

    for pattern in TEXT_SUSPICION_PATTERNS:
        pattern_re = re.compile(r"\b" + re.escape(pattern) + r"\b")
        if pattern_re.search(filename_text):
            matches.append(f"filename contains '{pattern}'")
            continue

        for match in pattern_re.finditer(summary_text):
            if not is_negated_text_hit(summary_text, match.start()):
                matches.append(f"summary contains '{pattern}'")
                break
    return matches


def suspicious_ignored_reasons(row: dict[str, Any]) -> list[str]:
    if clean_text(row.get("final_severity")).upper() != "IGNORE":
        return []

    reasons: list[str] = []
    max_motion_score = number_value(row.get("max_motion_score"))
    if max_motion_score is not None and max_motion_score >= HIGH_MOTION_SCORE_THRESHOLD:
        reasons.append(
            f"max_motion_score >= {HIGH_MOTION_SCORE_THRESHOLD:g} ({max_motion_score:g})"
        )

    if bool_value(row.get("strong_impact_like_motion")):
        reasons.append("strong_impact_like_motion true")
    if bool_value(row.get("possible_impact")):
        reasons.append("possible_impact true")
    if bool_value(row.get("possible_contact")):
        reasons.append("possible_contact true")

    impact_level = clean_text(row.get("impact_level")).upper()
    if impact_level in IMPORTANT_LEVELS:
        reasons.append(f"impact_level {impact_level}")

    contact_level = clean_text(row.get("contact_level")).upper()
    if contact_level in IMPORTANT_LEVELS:
        reasons.append(f"contact_level {contact_level}")

    reasons.extend(suspicious_text_reasons(row.get("video_filename"), row.get("summary")))
    return reasons


def has_thumbnail(incident: dict[str, Any], evidence: dict[str, Any]) -> bool:
    thumbnail_fields = [
        incident.get("hero_thumbnail"),
        incident.get("thumbnail"),
        incident.get("contact_sheet"),
        evidence.get("hero_thumbnail"),
        evidence.get("thumbnail"),
        evidence.get("contact_sheet"),
    ]
    return any(clean_text(value).strip() for value in thumbnail_fields)


def build_row(incident: dict[str, Any], index: int) -> dict[str, Any]:
    evidence = as_dict(incident.get("local_evidence"))
    if not evidence:
        evidence = as_dict(incident.get("local_evidence_summary"))
    debug = as_dict(incident.get("classification_debug"))

    row: dict[str, Any] = {
        "id": incident.get("id") or f"incident_{index:04d}",
        "event_timestamp": incident.get("event_timestamp", ""),
        "final_severity": incident.get("final_severity", incident.get("severity", "")),
        "event_type": incident.get("event_type", ""),
        "summary": incident.get("summary", ""),
        "camera_count": incident.get("camera_count", evidence.get("camera_count", "")),
        "video_filename": incident_video_filename(incident),
        "missing_thumbnail": not has_thumbnail(incident, evidence),
        "missing_local_evidence": not bool(evidence),
    }

    for field in LOCAL_EVIDENCE_FIELDS:
        row[field] = evidence_value(evidence, field)

    for field in CLASSIFICATION_DEBUG_FIELDS:
        row[field] = debug.get(field, "")

    reasons = suspicious_ignored_reasons(row)
    row["suspicious_ignored"] = bool(reasons)
    row["suspicious_reasons"] = reasons
    return row


def build_report(session: dict[str, Any]) -> dict[str, Any]:
    raw_incidents = as_list(session.get("incidents"))
    rows: list[dict[str, Any]] = []
    malformed_incidents = 0

    for index, incident in enumerate(raw_incidents, start=1):
        if not isinstance(incident, dict):
            malformed_incidents += 1
            incident = {"id": f"incident_{index:04d}", "summary": "Malformed incident entry"}
        rows.append(build_row(incident, index))

    severity_counts = {"IMPORTANT": 0, "REVIEW": 0, "IGNORE": 0}
    for row in rows:
        severity = clean_text(row.get("final_severity")).upper()
        if severity in severity_counts:
            severity_counts[severity] += 1

    suspicious = [row for row in rows if row.get("suspicious_ignored")]
    summary = {
        "incidents": len(rows),
        "important": severity_counts["IMPORTANT"],
        "review": severity_counts["REVIEW"],
        "ignore": severity_counts["IGNORE"],
        "suspicious_ignored": len(suspicious),
        "incidents_missing_thumbnails": sum(1 for row in rows if row.get("missing_thumbnail")),
        "incidents_missing_local_evidence": sum(
            1 for row in rows if row.get("missing_local_evidence")
        ),
        "malformed_incidents": malformed_incidents,
        "high_motion_score_threshold": HIGH_MOTION_SCORE_THRESHOLD,
    }
    return {
        "source": str(SESSION_PATH),
        "scanner_version": session.get("scanner_version", ""),
        "schema_version": session.get("schema_version", ""),
        "summary": summary,
        "incidents": rows,
        "suspicious_ignored_incidents": suspicious,
    }


def write_json_report(report: dict[str, Any]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with JSON_REPORT_PATH.open("w", encoding="utf-8") as file:
        json.dump(report, file, indent=2, ensure_ascii=False)


def write_csv_report(rows: list[dict[str, Any]]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with CSV_REPORT_PATH.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            csv_row = {field: compact_value(row.get(field, "")) for field in CSV_FIELDS}
            writer.writerow(csv_row)


def print_incident_row(row: dict[str, Any]) -> None:
    print()
    print(f"id: {clean_text(row.get('id'))}")
    print(f"event_timestamp: {clean_text(row.get('event_timestamp'))}")
    print(f"final_severity: {clean_text(row.get('final_severity'))}")
    print(f"event_type: {clean_text(row.get('event_type'))}")
    print(f"summary: {clean_text(row.get('summary'))}")
    print(f"camera_count: {clean_text(row.get('camera_count'))}")
    print(f"video_path filename: {clean_text(row.get('video_filename'))}")

    print("local_evidence:")
    for field in LOCAL_EVIDENCE_FIELDS:
        print(f"  {field}: {clean_text(row.get(field))}")

    print("classification_debug:")
    for field in CLASSIFICATION_DEBUG_FIELDS:
        print(f"  {field}: {clean_text(row.get(field))}")

    if row.get("suspicious_ignored"):
        print("SUSPICIOUS_IGNORED: YES")
        for reason in as_list(row.get("suspicious_reasons")):
            print(f"  - {reason}")
    else:
        print("suspicious_ignored: no")


def print_report(report: dict[str, Any]) -> None:
    print("Mimir Core v2 detection audit")
    print("=============================")
    print(f"source: {SESSION_PATH}")
    print(f"high motion threshold: {HIGH_MOTION_SCORE_THRESHOLD:g}")

    rows = as_list(report.get("incidents"))
    for row in rows:
        print_incident_row(as_dict(row))

    suspicious = as_list(report.get("suspicious_ignored_incidents"))
    print()
    print("Suspicious ignored incidents")
    print("============================")
    if suspicious:
        for row in suspicious:
            row_data = as_dict(row)
            reasons = "; ".join(clean_text(reason) for reason in as_list(row_data.get("suspicious_reasons")))
            print(
                f"- {clean_text(row_data.get('id'))} "
                f"{clean_text(row_data.get('event_timestamp'))} "
                f"{clean_text(row_data.get('video_filename'))}: {reasons}"
            )
    else:
        print("None found.")

    summary = as_dict(report.get("summary"))
    print()
    print("Summary")
    print("=======")
    print(f"incidents: {summary.get('incidents', 0)}")
    print(f"important: {summary.get('important', 0)}")
    print(f"review: {summary.get('review', 0)}")
    print(f"ignore: {summary.get('ignore', 0)}")
    print(f"suspicious ignored count: {summary.get('suspicious_ignored', 0)}")
    print(f"incidents missing thumbnails: {summary.get('incidents_missing_thumbnails', 0)}")
    print(
        "incidents missing local_evidence: "
        f"{summary.get('incidents_missing_local_evidence', 0)}"
    )
    print(f"json report: {JSON_REPORT_PATH}")
    print(f"csv report: {CSV_REPORT_PATH}")


def main() -> int:
    if not SESSION_PATH.exists():
        print(f"latest_session.json not found: {SESSION_PATH}")
        return 1

    try:
        session = read_json(SESSION_PATH)
    except json.JSONDecodeError as error:
        print(f"Could not parse latest_session.json: {error}")
        return 1
    except OSError as error:
        print(f"Could not read latest_session.json: {error}")
        return 1

    report = build_report(session)
    write_json_report(report)
    write_csv_report(as_list(report.get("incidents")))
    print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
