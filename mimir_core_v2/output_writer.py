"""Write Core v2 session output."""

from __future__ import annotations

import json
from pathlib import Path

from . import SCANNER_VERSION, SCHEMA_VERSION
from .event_grouping import GROUPING_VERSION, build_grouping_debug


TOP_LEVEL_EVIDENCE_FIELDS = [
    "motion_score",
    "max_motion_score",
    "localized_motion_score",
    "motion_spike_time_sec",
    "motion_spike_ratio",
    "camera_shake_score",
    "abrupt_scene_change",
    "scene_change_score",
    "strong_impact_like_motion",
    "possible_impact",
    "impact_level",
    "impact_score",
    "impact_evidence_reasons",
    "possible_contact",
    "contact_level",
    "contact_score",
    "contact_evidence_reasons",
    "person_detected",
    "vehicle_detected",
    "person_near_only",
    "person_passby",
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


def _person_count(evidence: dict) -> int:
    tracks = evidence.get("object_tracks")
    if isinstance(tracks, list):
        return sum(1 for track in tracks if isinstance(track, dict) and track.get("class_name") == "person")
    return 1 if evidence.get("person_detected") else 0


def _vehicle_count(evidence: dict) -> int:
    tracks = evidence.get("object_tracks")
    if isinstance(tracks, list):
        return sum(1 for track in tracks if isinstance(track, dict) and track.get("class_name") == "vehicle")
    return 1 if evidence.get("vehicle_detected") else 0


def incident_from_group(index: int, event_group: dict, evidence: dict, severity: dict, ai_review: dict) -> dict:
    primary_camera = severity.get("primary_camera") or "unknown"
    primary_clip = None
    for clip in event_group.get("clips", []):
        if clip.get("camera") == primary_camera:
            primary_clip = clip
            break
    if primary_clip is None and event_group.get("clips"):
        primary_clip = event_group["clips"][0]

    video_path = primary_clip.get("path") if isinstance(primary_clip, dict) else ""
    source_filename = (
        primary_clip.get("filename")
        if isinstance(primary_clip, dict) and primary_clip.get("filename")
        else event_group.get("source_filename", "")
    )
    source_stem = Path(str(source_filename)).stem if source_filename else event_group.get("source_stem", "")
    display_timestamp = event_group.get("display_timestamp", "")
    display_title = event_group.get("display_title") or source_stem or display_timestamp

    incident = {
        "id": f"incident_{index:04d}",
        "event_group_id": event_group.get("event_group_id", ""),
        "event_timestamp": event_group.get("event_timestamp", ""),
        "display_title": display_title,
        "display_timestamp": display_timestamp,
        "source_filename": source_filename,
        "source_stem": source_stem,
        "filename_timestamp_detected": bool(event_group.get("filename_timestamp_detected")),
        "event_folder": event_group.get("event_folder", ""),
        "source_category": event_group.get("source_category", ""),
        "severity": severity.get("severity", "IGNORE"),
        "final_severity": severity.get("final_severity", severity.get("severity", "IGNORE")),
        "event_type": severity.get("event_type", "event"),
        "summary": severity.get("summary", ""),
        "camera_count": event_group.get("camera_count", 0),
        "available_cameras": event_group.get("available_cameras", []),
        "primary_camera": primary_camera,
        "camera_clips": event_group.get("clips", []),
        "video_path": video_path or "",
        "thumbnail": evidence.get("thumbnail"),
        "hero_thumbnail": evidence.get("hero_thumbnail", ""),
        "contact_sheet": evidence.get("contact_sheet", ""),
        "timeline_markers": evidence.get("timeline_markers", []),
        "local_evidence": evidence,
        "local_evidence_summary": evidence,
        "ai_evidence": ai_review.get("ai_evidence", {}),
        "ai_raw_response": ai_review.get("ai_raw_response", ""),
        "ai_parse_error": bool(ai_review.get("ai_parse_error")),
        "ai_reviewed": bool(ai_review.get("ai_reviewed")),
        "ai_review_skipped_reason": ai_review.get("ai_review_skipped_reason", ""),
        "ai_model": ai_review.get("ai_model") or ai_review.get("model", ""),
        "ai_evidence_review": ai_review,
        "severity_reasons": severity.get("severity_reasons", []),
        "classification_debug": severity.get("classification_debug", {}),
        "warnings": evidence.get("evidence_warnings", []),
    }
    for field in TOP_LEVEL_EVIDENCE_FIELDS:
        if field in evidence:
            incident[field] = evidence.get(field)
    incident["persons"] = _person_count(evidence)
    incident["vehicles"] = _vehicle_count(evidence)
    incident["impact_reasons"] = evidence.get("impact_evidence_reasons", [])
    incident["contact_reasons"] = evidence.get("contact_evidence_reasons", [])
    incident["impact_evidence_level"] = evidence.get("impact_level", "NONE")
    incident["contact_evidence_level"] = evidence.get("contact_level", "NONE")
    incident["important_evidence_found"] = incident["classification_debug"].get("important_evidence_found")
    incident["severity_cap_applied"] = incident["classification_debug"].get("severity_cap_applied")
    incident["severity_cap_reason"] = incident["classification_debug"].get("severity_cap_reason")
    return incident


def build_session(selected_input: str, event_groups: list[dict], incidents: list[dict], warnings: list[str]) -> dict:
    important = sum(1 for incident in incidents if incident.get("final_severity") == "IMPORTANT")
    review = sum(1 for incident in incidents if incident.get("final_severity") == "REVIEW")
    ignore = sum(1 for incident in incidents if incident.get("final_severity") == "IGNORE")

    return {
        "schema_version": SCHEMA_VERSION,
        "scanner_version": SCANNER_VERSION,
        "selected_input": selected_input,
        "grouping_version": GROUPING_VERSION,
        "grouping_debug": build_grouping_debug(event_groups, warnings),
        "event_groups_found": len(event_groups),
        "multi_camera_groups": sum(1 for group in event_groups if int(group.get("camera_count") or 0) > 1),
        "single_camera_groups": sum(1 for group in event_groups if int(group.get("camera_count") or 0) == 1),
        "generic_filename_groups": sum(1 for group in event_groups if group.get("generic_filename_group")),
        "tesla_timestamp_groups": sum(1 for group in event_groups if group.get("tesla_timestamp_detected")),
        "incident_count": len(incidents),
        "important": important,
        "review": review,
        "ignore": ignore,
        "incidents": incidents,
        "warnings": warnings,
    }


def write_latest_session(session: dict, output_folder: str | Path) -> Path:
    folder = Path(output_folder)
    folder.mkdir(parents=True, exist_ok=True)
    output_path = folder / "latest_session.json"
    with output_path.open("w", encoding="utf-8") as file:
        json.dump(session, file, indent=2)
    return output_path
