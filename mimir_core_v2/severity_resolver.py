"""Strict evidence-based severity resolution for Mimir Core v2."""

from __future__ import annotations

from .validators import CAMERA_PRIORITY


RESOLVER_VERSION = "safe_resolver_v2"
SEVERITY_RANK = {"IGNORE": 0, "REVIEW": 1, "IMPORTANT": 2}
HIGH_MOTION_SPIKE_THRESHOLD = 0.85


def _bool(data: dict, field: str) -> bool:
    return bool(data.get(field))


def _level(data: dict, field: str) -> str:
    return str(data.get(field) or "NONE").strip().upper()


def _severity(value: object) -> str:
    text = str(value or "").strip().upper()
    return text if text in SEVERITY_RANK else "IGNORE"


def _max_severity(left: str, right: str) -> str:
    return left if SEVERITY_RANK[left] >= SEVERITY_RANK[right] else right


def _min_severity(left: str, right: str) -> str:
    return left if SEVERITY_RANK[left] <= SEVERITY_RANK[right] else right


def _motion_spike(evidence: dict) -> bool:
    if _bool(evidence, "strong_motion_spike") or _bool(evidence, "impact_like_motion_spike"):
        return True

    try:
        return float(evidence.get("motion_score") or 0.0) >= HIGH_MOTION_SPIKE_THRESHOLD
    except (TypeError, ValueError):
        return False


def important_evidence_reasons(evidence: dict) -> list[str]:
    reasons: list[str] = []

    if _bool(evidence, "crash_safety_triggered"):
        reasons.append("crash_safety_triggered")
    if _level(evidence, "impact_level") == "HIGH":
        reasons.append("impact_level=HIGH")
    if _level(evidence, "contact_level") == "HIGH":
        reasons.append("contact_level=HIGH")
    if _bool(evidence, "visible_contact"):
        reasons.append("visible_contact")
    if _bool(evidence, "visible_impact"):
        reasons.append("visible_impact")
    if _bool(evidence, "person_interaction_evidence"):
        reasons.append("person_interaction_evidence")
    if _bool(evidence, "tampering_evidence"):
        reasons.append("tampering_evidence")
    if _bool(evidence, "door_handle_attempt"):
        reasons.append("door_handle_attempt")
    if _motion_spike(evidence):
        reasons.append("strong impact-like motion spike")

    return reasons


def _contact_or_impact_or_tampering(evidence: dict) -> bool:
    return bool(
        important_evidence_reasons(evidence)
        or _bool(evidence, "possible_impact")
        or _bool(evidence, "possible_contact")
        or _level(evidence, "impact_level") in {"LOW", "MEDIUM", "HIGH"}
        or _level(evidence, "contact_level") in {"LOW", "MEDIUM", "HIGH"}
        or _bool(evidence, "tampering_evidence")
    )


def _ai_recommendation(ai_review: dict | None) -> str:
    if not isinstance(ai_review, dict):
        return "IGNORE"

    ai_evidence = ai_review.get("ai_evidence")
    if isinstance(ai_evidence, dict) and ai_evidence.get("recommended_severity"):
        return _severity(ai_evidence.get("recommended_severity"))

    return _severity(
        ai_review.get("recommended_severity")
        or ai_review.get("recommendation")
        or ai_review.get("severity")
    )


def choose_primary_camera(event_group: dict, evidence: dict) -> str:
    cameras = event_group.get("available_cameras")
    if not isinstance(cameras, list) or not cameras:
        return "unknown"

    camera_evidence = evidence.get("camera_evidence") if isinstance(evidence.get("camera_evidence"), dict) else {}
    if camera_evidence:
        best_camera, best_score = "unknown", -1.0
        for camera, details in camera_evidence.items():
            if camera not in cameras or not isinstance(details, dict):
                continue
            score = float(details.get("evidence_score") or 0.0)
            if score > best_score:
                best_camera, best_score = camera, score
        if best_camera != "unknown" and best_score > 0:
            return best_camera

    for camera in ["back", "rear", "front", *CAMERA_PRIORITY]:
        if camera in cameras:
            return camera

    return str(cameras[0])


def resolve_severity(event_group: dict, evidence: dict, ai_review: dict) -> dict:
    local_evidence = evidence if isinstance(evidence, dict) else {}
    ai_evidence = ai_review if isinstance(ai_review, dict) else {}
    has_video = bool(local_evidence.get("has_video"))
    event_type = "multi_camera_event" if evidence.get("multi_camera") else "single_camera_event"
    reasons: list[str] = []
    debug = {
        "resolver_version": RESOLVER_VERSION,
        "important_evidence_found": False,
        "important_evidence_reasons": [],
        "severity_cap_applied": False,
        "severity_cap_reason": "",
        "severity_floor_applied": False,
        "severity_floor_reason": "",
        "ai_blocked_reason": "",
    }

    if not has_video:
        summary = "Mimir found an event group, but no video file could be confirmed."
    elif evidence.get("multi_camera"):
        summary = "Mimir grouped multiple camera angles into one event."
    else:
        summary = "Mimir found one video for this event."

    hard_reasons = important_evidence_reasons(local_evidence)
    hard_important = bool(hard_reasons)
    debug["important_evidence_found"] = hard_important
    debug["important_evidence_reasons"] = hard_reasons

    severity = "IGNORE" if has_video else "REVIEW"
    if not has_video:
        reasons.append("Video file could not be confirmed.")

    if _bool(local_evidence, "normal_traffic"):
        severity = "IGNORE"
        reasons.append("Normal traffic evidence.")

    if _bool(local_evidence, "person_near_only"):
        severity = _max_severity(severity, "REVIEW")
        reasons.append("Person near vehicle without contact or tampering evidence.")

    if _bool(local_evidence, "person_passby") or _bool(local_evidence, "person_passby_detected"):
        severity = _max_severity(severity, "IGNORE")
        reasons.append("Brief person pass-by evidence.")

    if _bool(local_evidence, "possible_contact"):
        severity = _max_severity(severity, "REVIEW")
        reasons.append("Possible contact evidence.")

    if _bool(local_evidence, "uncertain"):
        severity = _max_severity(severity, "REVIEW")
        reasons.append("Uncertain local evidence.")

    if _bool(local_evidence, "crash_safety_triggered"):
        if SEVERITY_RANK[severity] < SEVERITY_RANK["REVIEW"]:
            debug["severity_floor_applied"] = True
            debug["severity_floor_reason"] = "crash_safety_triggered requires at least REVIEW"
        severity = _max_severity(severity, "REVIEW")
        reasons.append("Crash safety trigger.")

    if hard_important:
        severity = "IMPORTANT"
        reasons.extend(hard_reasons)

    ai_recommendation = _ai_recommendation(ai_evidence)
    if ai_recommendation == "IMPORTANT":
        if hard_important:
            severity = "IMPORTANT"
            reasons.append("AI supported by hard local evidence.")
        else:
            debug["ai_blocked_reason"] = "AI escalation blocked: no hard contact, impact, or tampering evidence."
            reasons.append(debug["ai_blocked_reason"])
    elif ai_recommendation == "REVIEW":
        if not _bool(local_evidence, "normal_traffic") or hard_important:
            severity = _max_severity(severity, "REVIEW")
            reasons.append("AI recommended review.")

    cap_reason = ""
    if not hard_important:
        if _bool(local_evidence, "normal_traffic"):
            cap_reason = "normal_traffic capped at IGNORE without hard evidence"
            severity = _min_severity(severity, "IGNORE")
        elif _bool(local_evidence, "person_passby") or _bool(local_evidence, "person_passby_detected"):
            if not _contact_or_impact_or_tampering(local_evidence):
                cap_reason = "person pass-by capped at IGNORE without contact, impact, or tampering"
                severity = _min_severity(severity, "IGNORE")
            else:
                cap_reason = "person pass-by capped at REVIEW without hard evidence"
                severity = _min_severity(severity, "REVIEW")
        elif _bool(local_evidence, "person_near_only"):
            cap_reason = "person near only capped at REVIEW without contact, impact, or tampering"
            severity = _min_severity(severity, "REVIEW")
        elif _bool(local_evidence, "possible_contact") and _level(local_evidence, "contact_level") != "HIGH":
            cap_reason = "weak possible contact capped at REVIEW without high contact or visible contact"
            severity = _min_severity(severity, "REVIEW")

    if cap_reason:
        debug["severity_cap_applied"] = True
        debug["severity_cap_reason"] = cap_reason
        reasons.append(cap_reason)

    if severity == "IMPORTANT":
        if _level(local_evidence, "impact_level") == "HIGH":
            summary = "Mimir found high impact evidence."
        elif _level(local_evidence, "contact_level") == "HIGH":
            summary = "Mimir found high contact evidence."
        else:
            summary = "Mimir found hard Important evidence."
    elif _bool(local_evidence, "person_near_only"):
        summary = "Mimir saw a person near the vehicle, but no clear contact or tampering was detected."
    elif _bool(local_evidence, "normal_traffic"):
        summary = "Mimir saw ordinary traffic with no contact or impact evidence."
    elif severity == "REVIEW":
        summary = "Mimir found uncertain activity worth review."

    return {
        "severity": severity,
        "final_severity": severity,
        "event_type": event_type,
        "summary": summary,
        "primary_camera": choose_primary_camera(event_group, local_evidence),
        "severity_reasons": reasons or ["No concerning evidence found."],
        "classification_debug": debug,
    }
