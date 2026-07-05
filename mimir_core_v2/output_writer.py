"""Write Core v2 session output."""

from __future__ import annotations

import json
from pathlib import Path

from . import SCANNER_VERSION, SCHEMA_VERSION
from .event_grouping import GROUPING_VERSION, build_grouping_debug


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

    return {
        "id": f"incident_{index:04d}",
        "event_group_id": event_group.get("event_group_id", ""),
        "event_timestamp": event_group.get("event_timestamp", ""),
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
        "ai_evidence_review": ai_review,
        "severity_reasons": severity.get("severity_reasons", []),
        "classification_debug": severity.get("classification_debug", {}),
    }


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
