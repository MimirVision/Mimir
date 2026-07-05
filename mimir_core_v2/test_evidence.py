"""Evidence validation for Mimir Core v2 output.

Run:
    python -m mimir_core_v2.test_evidence --input "D:\\TeslaCam\\SentryClips\\2026-04-18_16-04-02"
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


DEFAULT_SESSION = Path(r"C:\Mimir_Backend\MimirOutputV2\latest_session.json")

REQUIRED_EVIDENCE_KEYS = [
    "motion_score",
    "max_motion_score",
    "motion_spike_time_sec",
    "strong_impact_like_motion",
    "possible_impact",
    "impact_level",
    "possible_contact",
    "contact_level",
    "person_detected",
    "vehicle_detected",
    "person_near_only",
    "person_passby_detected",
    "person_lingering_detected",
    "vehicle_passby_detected",
    "vehicle_lingering_detected",
    "normal_traffic",
    "normal_traffic_evidence",
    "visible_contact",
    "visible_impact",
    "person_interaction_evidence",
    "tampering_evidence",
    "door_handle_attempt",
    "crash_safety_triggered",
]

HARD_EVIDENCE_FIELDS = [
    "visible_contact",
    "visible_impact",
    "person_interaction_evidence",
    "tampering_evidence",
    "door_handle_attempt",
    "crash_safety_triggered",
]


def load_session(path: Path) -> tuple[dict | None, str]:
    if not path.exists():
        return None, f"session not found: {path}"
    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"could not read session: {exc}"
    if not isinstance(data, dict):
        return None, "session JSON is not an object"
    return data, ""


def severity(incident: dict) -> str:
    return str(incident.get("final_severity") or incident.get("severity") or "").upper()


def has_hard_evidence(evidence: dict) -> bool:
    if any(bool(evidence.get(field)) for field in HARD_EVIDENCE_FIELDS):
        return True
    if str(evidence.get("impact_level") or "").upper() == "HIGH":
        return True
    if str(evidence.get("contact_level") or "").upper() == "HIGH":
        return True
    if bool(evidence.get("strong_impact_like_motion")):
        return True
    return False


def validate_incident(incident: dict) -> list[str]:
    problems: list[str] = []
    incident_id = incident.get("id", "unknown")
    evidence = incident.get("local_evidence")
    if not isinstance(evidence, dict):
        return [f"{incident_id}: local_evidence is missing or not an object"]

    missing = [key for key in REQUIRED_EVIDENCE_KEYS if key not in evidence]
    if missing:
        problems.append(f"{incident_id}: local_evidence missing keys: {missing}")

    important = severity(incident) == "IMPORTANT"
    person_or_passby = bool(
        evidence.get("person_passby_detected")
        or evidence.get("person_passby")
        or evidence.get("person_near_only")
        or evidence.get("normal_traffic")
        or evidence.get("normal_traffic_evidence")
    )
    if important and person_or_passby and not has_hard_evidence(evidence):
        problems.append(f"{incident_id}: pass-by/person-near/normal traffic became IMPORTANT without hard evidence")

    weak_contact = bool(evidence.get("possible_contact")) and str(evidence.get("contact_level") or "").upper() in {
        "NONE",
        "LOW",
        "MEDIUM",
    }
    if important and weak_contact and not has_hard_evidence(evidence):
        problems.append(f"{incident_id}: weak possible_contact became IMPORTANT without hard evidence")

    for field in HARD_EVIDENCE_FIELDS:
        if field in evidence and not isinstance(evidence.get(field), bool):
            problems.append(f"{incident_id}: hard evidence field {field} is not boolean")

    return problems


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate Mimir Core v2 local evidence.")
    parser.add_argument("--input", required=False, help="Input folder used for the latest v2 scan; shown for context only.")
    parser.add_argument("--session", default=str(DEFAULT_SESSION), help="Path to MimirOutputV2/latest_session.json.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    session, error = load_session(Path(args.session))

    print("Mimir Core v2 Evidence Test")
    print("===========================")
    if args.input:
        print(f"input: {args.input}")

    if error:
        print(error)
        print()
        print("EVIDENCE FAILED")
        return 1

    incidents = session.get("incidents") if isinstance(session, dict) else []
    if not isinstance(incidents, list):
        print("incidents is missing or not a list")
        print()
        print("EVIDENCE FAILED")
        return 1

    problems: list[str] = []
    for incident in incidents:
        if isinstance(incident, dict):
            problems.extend(validate_incident(incident))
        else:
            problems.append("incident entry is not an object")

    print(f"incidents checked: {len(incidents)}")
    print(f"evidence_version: {session.get('evidence_version', 'missing')}")
    debug = session.get("evidence_debug") if isinstance(session.get("evidence_debug"), dict) else {}
    print(f"groups_processed: {debug.get('groups_processed', 'missing')}")
    print(f"groups_with_frames: {debug.get('groups_with_frames', 'missing')}")
    print(f"groups_without_frames: {debug.get('groups_without_frames', 'missing')}")
    print(f"yolo_available: {debug.get('yolo_available', 'missing')}")
    print(f"yolo_failures: {debug.get('yolo_failures', 'missing')}")

    if problems:
        print()
        for problem in problems:
            print(f"- {problem}")
        print()
        print("EVIDENCE FAILED")
        return 1

    print()
    print("EVIDENCE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

