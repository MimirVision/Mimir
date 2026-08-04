import json
import os
import sys


SESSION_PATH = r"C:\Mimir_Backend\MimirOutputV2\latest_session.json"

VALID_DECISIONS = {"IMPORTANT", "REVIEW", "IGNORE"}
VALID_IMPACT_LEVELS = {"NONE", "LOW", "MEDIUM", "HIGH"}
VALID_CONTACT_LEVELS = {"NONE", "LOW", "MEDIUM", "HIGH"}
VALID_MARKER_SEVERITIES = {"IMPORTANT", "REVIEW", "IGNORE", "NEUTRAL"}
VALID_MARKER_TYPES = {
    "event_started",
    "person_detected",
    "vehicle_detected",
    "event_peak",
    "possible_impact",
    "possible_contact",
    "vehicle_nearby",
    "event_ended",
}

REQUIRED_TOP_FIELDS = [
    "status",
    "started_at",
    "finished_at",
    "clips_processed",
    "important",
    "review",
    "ignore",
    "incidents",
]

OPTIONAL_TOP_TYPES = {
    "input_folder": str,
    "safe_input_mode": bool,
    "event_json_files_found": int,
    "source_events_found": int,
    "tesla_events_found": int,
    "safety_rules_version": str,
}

INCIDENT_FIELD_TYPES = {
    "id": str,
    "source_video": str,
    "event_id": int,
    "severity": str,
    "ai_decision": str,
    "score": (int, float),
    "persons": int,
    "vehicles": int,
    "active_frames": int,
    "thumbnail": str,
    "contact_sheet": str,
    "start_frame_image": str,
    "best_frame_image": str,
    "end_frame_image": str,
    "ai_confidence": (int, float),
    "event_type": str,
    "summary": str,
    "evidence": list,
    "recommended_action": str,
    "timeline_markers": list,
    "timeline_quality": dict,
    "possible_impact": bool,
    "crash_safety_triggered": bool,
    "motion_triggered": bool,
    "trigger_reasons": list,
    "impact_level": str,
    "impact_score": (int, float),
    "impact_reasons": list,
    "contact_score": (int, float),
    "contact_level": str,
    "possible_contact": bool,
    "contact_reasons": list,
    "max_motion_score": (int, float),
    "motion_spike_time_sec": (int, float, type(None)),
    "source_event_timestamp": (str, type(None)),
    "source_event_reason": (str, type(None)),
    "source_event_city": (str, type(None)),
    "source_event_est_lat": (int, float, str, type(None)),
    "source_event_est_lon": (int, float, str, type(None)),
    "source_event_raw": (dict, list, str, int, float, bool, type(None)),
    "tesla_event_timestamp": (str, type(None)),
    "tesla_event_reason": (str, type(None)),
    "tesla_event_city": (str, type(None)),
    "tesla_event_est_lat": (int, float, str, type(None)),
    "tesla_event_est_lon": (int, float, str, type(None)),
    "tesla_event_raw": (dict, list, str, int, float, bool, type(None)),
}

IMAGE_PATH_FIELDS = [
    "thumbnail",
    "contact_sheet",
    "start_frame_image",
    "best_frame_image",
    "end_frame_image",
]

MARKER_FIELD_TYPES = {
    "time_sec": (int, float),
    "frame_index": int,
    "type": str,
    "severity": str,
    "label": str,
    "description": str,
}


def type_name(expected_type):
    if isinstance(expected_type, tuple):
        return " or ".join(
            item.__name__
            for item in expected_type
        )

    return expected_type.__name__


def add_error(errors, message):
    errors.append(f"ERROR: {message}")


def add_warning(warnings, message):
    warnings.append(f"WARNING: {message}")


def validate_type(errors, path, value, expected_type):
    if not isinstance(value, expected_type):
        add_error(
            errors,
            f"{path} must be {type_name(expected_type)}, got {type(value).__name__}"
        )


def validate_required_top_fields(data, errors):
    for field in REQUIRED_TOP_FIELDS:
        if field not in data:
            add_error(errors, f"Missing top-level field: {field}")


def validate_top_level(data, errors):
    validate_required_top_fields(data, errors)

    if "incidents" in data:
        validate_type(errors, "incidents", data["incidents"], list)

    for field in ["clips_processed", "important", "review", "ignore"]:
        if field in data:
            validate_type(errors, field, data[field], int)

    for field in ["status", "started_at"]:
        if field in data:
            validate_type(errors, field, data[field], str)

    if "finished_at" in data:
        validate_type(errors, "finished_at", data["finished_at"], (str, type(None)))

    for field, expected_type in OPTIONAL_TOP_TYPES.items():
        if field in data:
            validate_type(errors, field, data[field], expected_type)


def validate_enum(errors, path, value, allowed):
    if value not in allowed:
        add_error(
            errors,
            f"{path} must be one of {', '.join(sorted(allowed))}; got {value!r}"
        )


def validate_marker(marker, incident_index, marker_index, errors):
    if not isinstance(marker, dict):
        add_error(
            errors,
            f"incidents[{incident_index}].timeline_markers[{marker_index}] must be an object"
        )
        return

    base = f"incidents[{incident_index}].timeline_markers[{marker_index}]"

    for field, expected_type in MARKER_FIELD_TYPES.items():
        if field in marker:
            validate_type(errors, f"{base}.{field}", marker[field], expected_type)

    if "severity" in marker:
        validate_enum(
            errors,
            f"{base}.severity",
            marker["severity"],
            VALID_MARKER_SEVERITIES
        )

    if "type" in marker:
        validate_enum(
            errors,
            f"{base}.type",
            marker["type"],
            VALID_MARKER_TYPES
        )


def validate_incident(incident, index, errors, warnings):
    if not isinstance(incident, dict):
        add_error(errors, f"incidents[{index}] must be an object")
        return

    for field, expected_type in INCIDENT_FIELD_TYPES.items():
        if field in incident:
            validate_type(errors, f"incidents[{index}].{field}", incident[field], expected_type)

    if "severity" in incident:
        validate_enum(
            errors,
            f"incidents[{index}].severity",
            incident["severity"],
            VALID_DECISIONS
        )

    if "ai_decision" in incident:
        validate_enum(
            errors,
            f"incidents[{index}].ai_decision",
            incident["ai_decision"],
            VALID_DECISIONS
        )

    if "impact_level" in incident:
        validate_enum(
            errors,
            f"incidents[{index}].impact_level",
            incident["impact_level"],
            VALID_IMPACT_LEVELS
        )

    if "contact_level" in incident:
        validate_enum(
            errors,
            f"incidents[{index}].contact_level",
            incident["contact_level"],
            VALID_CONTACT_LEVELS
        )

    if "timeline_markers" in incident:
        markers = incident["timeline_markers"]

        if isinstance(markers, list):
            for marker_index, marker in enumerate(markers):
                validate_marker(marker, index, marker_index, errors)

    if "evidence" in incident and not isinstance(incident["evidence"], list):
        add_error(errors, f"incidents[{index}].evidence must be a list")

    if "impact_reasons" in incident and not isinstance(incident["impact_reasons"], list):
        add_error(errors, f"incidents[{index}].impact_reasons must be a list")

    if "contact_reasons" in incident and not isinstance(incident["contact_reasons"], list):
        add_error(errors, f"incidents[{index}].contact_reasons must be a list")

    incident_id = incident.get("id", f"incident[{index}]")

    for field in IMAGE_PATH_FIELDS:
        path = incident.get(field)

        if path and isinstance(path, str) and not os.path.exists(path):
            add_warning(
                warnings,
                f"{incident_id}: {field} file does not exist: {path}"
            )


def load_session():
    if not os.path.exists(SESSION_PATH):
        raise FileNotFoundError(f"Session file does not exist: {SESSION_PATH}")

    with open(SESSION_PATH, "r", encoding="utf-8") as file:
        data = json.load(file)

    if not isinstance(data, dict):
        raise ValueError("latest_session.json must contain a JSON object")

    return data


def print_summary(data, errors, warnings):
    incidents = data.get("incidents", [])
    incident_count = len(incidents) if isinstance(incidents, list) else 0

    print("Mimir Output Validation")
    print("=======================")
    print(f"Result: {'FAILED' if errors else 'PASSED'}")
    print(f"Clips processed: {data.get('clips_processed', 'N/A')}")
    print(f"Incident count: {incident_count}")
    print(f"Important: {data.get('important', 'N/A')}")
    print(f"Review: {data.get('review', 'N/A')}")
    print(f"Ignore: {data.get('ignore', 'N/A')}")
    print(f"Warnings: {len(warnings)}")

    if warnings:
        print("\nWarnings")
        print("--------")
        for warning in warnings:
            print(warning)

    if errors:
        print("\nErrors")
        print("------")
        for error in errors:
            print(error)


def main():
    errors = []
    warnings = []

    try:
        data = load_session()
    except Exception as exc:
        print("Mimir Output Validation")
        print("=======================")
        print("Result: FAILED")
        print(f"ERROR: {exc}")
        sys.exit(1)

    validate_top_level(data, errors)

    incidents = data.get("incidents", [])

    if isinstance(incidents, list):
        for index, incident in enumerate(incidents):
            validate_incident(incident, index, errors, warnings)

    print_summary(data, errors, warnings)

    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
