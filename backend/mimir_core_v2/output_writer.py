"""Write Core v2 session output."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import (
    CORE_BUILD_ID,
    CORE_VERSION,
    FEATURE_FLAGS,
    GENERATED_BY,
    SCAN_COMMAND_VERSION,
    SCANNER_VERSION,
    SCHEMA_VERSION,
)
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
    "object_detection_available",
    # How many detections were discarded for covering the whole frame. Without
    # it a session where the detector failed on every frame is indistinguishable
    # from one where nothing happened.
    "frame_filling_detections",
    "strong_impact_like_motion",
    "possible_impact",
    "impact_level",
    "impact_score",
    "impact_evidence_reasons",
    "no_yolo_motion_impact_candidate",
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
    "crash_safety_reasons",
    "key_moments",
    "primary_key_moment_sec",
    "primary_key_moment_label",
    "key_moment_version",
    "key_moment_refinement",
    "key_moment_refinement_version",
    "contact_timing_confidence",
    "contact_timing_uncertain",
    "ego_vehicle_mask_source",
    "contact_candidate",
    "contact_verified",
    "apparent_visual_contact",
    "uncertain_close_activity",
    "confidence_provenance",
    "primary_key_moment_context",
]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def backend_runtime_name() -> str:
    if getattr(sys, "frozen", False):
        return "exe"
    return "python_script"


def apply_session_metadata(session: dict, output_path: Path) -> None:
    feature_flags = dict(FEATURE_FLAGS)
    existing_flags = session.get("feature_flags")
    if isinstance(existing_flags, dict):
        feature_flags.update(existing_flags)

    session.setdefault("core_version", CORE_VERSION)
    session.setdefault("core_build_id", CORE_BUILD_ID)
    session.setdefault("generated_by", GENERATED_BY)
    session.setdefault("backend_runtime", backend_runtime_name())
    session.setdefault("scan_command_version", SCAN_COMMAND_VERSION)
    session["feature_flags"] = feature_flags
    session.setdefault("session_created_at", _utc_now_iso())
    session["output_path"] = str(output_path.resolve())


def _write_json_atomically(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        json.dump(data, file, indent=2)
        file.write("\n")
    os.replace(temporary, path)


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


# camera_evidence and object_tracks are dense per-frame diagnostic dumps
# (motion signals, detection boxes) used only while resolve_severity() and
# refine_key_moment() compute this incident, both already complete by the
# time this function runs -- nothing reads them afterward. Not the frontend
# (grep confirms zero references), not the deferred AI enrichment pass
# (ai_reviewer.py's AI_LOCAL_EVIDENCE_KEYS allowlist doesn't include them),
# not Rust. Each field alone runs ~1MB per incident; persisting them anyway
# was the direct cause of a real 679-clip session.json reaching 777MB, which
# meant several GB of RAM and minutes to parse+render on every launch --
# indistinguishable, to a user, from Mimir simply not working.
_PERSISTED_EVIDENCE_EXCLUDED_KEYS = frozenset({"camera_evidence", "object_tracks"})


def _evidence_for_storage(evidence: dict) -> dict:
    if not isinstance(evidence, dict):
        return {}
    return {key: value for key, value in evidence.items() if key not in _PERSISTED_EVIDENCE_EXCLUDED_KEYS}


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
    ai_evidence = ai_review.get("ai_evidence", {}) if isinstance(ai_review.get("ai_evidence"), dict) else {}

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
        "key_moments": evidence.get("key_moments", []),
        "primary_key_moment_sec": evidence.get("primary_key_moment_sec", 0.0),
        "primary_key_moment_label": evidence.get("primary_key_moment_label", ""),
        "key_moment_version": evidence.get("key_moment_version", ""),
        "local_evidence": _evidence_for_storage(evidence),
        "ai_evidence": ai_evidence,
        "ai_raw_response": ai_review.get("ai_raw_response", ""),
        "ai_parse_error": bool(ai_review.get("ai_parse_error")),
        "ai_reviewed": bool(ai_review.get("ai_reviewed")),
        "ai_review_skipped_reason": ai_review.get("ai_review_skipped_reason", ""),
        "ai_model": ai_review.get("ai_model") or ai_review.get("model", ""),
        "ai_debug_dir": ai_review.get("ai_debug_dir", ""),
        "ai_prompt_version": ai_review.get("ai_prompt_version", ""),
        "ai_images_used": ai_review.get("ai_images_used", []),
        "ai_image_count": ai_review.get("ai_image_count", 0),
        "ai_scene_type": ai_evidence.get("scene_type", ""),
        "ai_recommended_severity": ai_evidence.get("recommended_severity", ai_review.get("recommended_severity", "")),
        "ai_confidence": ai_evidence.get("confidence", 0.0),
        "ai_concerns": ai_evidence.get("concerns", []),
        "ai_runtime_sec": ai_review.get("runtime_sec", 0.0),
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
    incident["contact_candidates"] = []
    if evidence.get("contact_candidate") or evidence.get("possible_contact"):
        incident["contact_candidates"].append(
            {
                "time_sec": evidence.get("primary_key_moment_sec") or evidence.get("motion_spike_time_sec"),
                "camera": primary_camera,
                "apparent_visual_contact": bool(evidence.get("apparent_visual_contact")),
                "contact_verified": bool(evidence.get("contact_verified")),
                "timing_confidence": evidence.get("contact_timing_confidence", 0.0),
                "timing_uncertain": bool(evidence.get("contact_timing_uncertain", True)),
                "reasons": evidence.get("contact_evidence_reasons", []),
            }
        )
    incident["important_evidence_found"] = incident["classification_debug"].get("important_evidence_found")
    incident["severity_cap_applied"] = incident["classification_debug"].get("severity_cap_applied")
    incident["severity_cap_reason"] = incident["classification_debug"].get("severity_cap_reason")
    return incident


def build_session(selected_input: str, event_groups: list[dict], incidents: list[dict], warnings: list[str]) -> dict:
    important = sum(1 for incident in incidents if incident.get("final_severity") == "IMPORTANT")
    review = sum(1 for incident in incidents if incident.get("final_severity") == "REVIEW")
    ignore = sum(1 for incident in incidents if incident.get("final_severity") == "IGNORE")
    key_moment_versions = [incident.get("key_moment_version") for incident in incidents if incident.get("key_moment_version")]

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
        "key_moment_version": key_moment_versions[0] if key_moment_versions else "",
        "key_moments_generated_count": sum(
            len(incident.get("key_moments") or [])
            for incident in incidents
            if isinstance(incident.get("key_moments"), list)
        ),
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
    apply_session_metadata(session, output_path)
    session_id = str(session.get("session_id") or "").strip()
    session_output_dir = Path(str(session.get("session_output_dir") or "")) if session.get("session_output_dir") else None
    if session_output_dir is None and session_id:
        session_output_dir = folder / "sessions" / session_id
    if session_output_dir is not None:
        archive_path = session_output_dir / "session.json"
        session["session_archive_path"] = str(archive_path.resolve())
        _write_json_atomically(archive_path, session)
    _write_json_atomically(output_path, session)
    return output_path
