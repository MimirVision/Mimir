"""Strict evidence-based severity resolution for Mimir Core v2."""

from __future__ import annotations

from .validators import CAMERA_PRIORITY


RESOLVER_VERSION = "safe_resolver_v2"
SEVERITY_RANK = {"IGNORE": 0, "REVIEW": 1, "IMPORTANT": 2}


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


def _has_contact_impact_or_tampering(evidence: dict) -> bool:
    return bool(
        _bool(evidence, "visible_contact")
        or _bool(evidence, "visible_impact")
        or _bool(evidence, "person_interaction_evidence")
        or _bool(evidence, "tampering_evidence")
        or _bool(evidence, "door_handle_attempt")
        or _bool(evidence, "possible_impact")
        or _bool(evidence, "possible_contact")
        or _level(evidence, "impact_level") in {"LOW", "MEDIUM", "HIGH"}
        or _level(evidence, "contact_level") in {"LOW", "MEDIUM", "HIGH"}
    )


def important_evidence_reasons(local_evidence: dict) -> list[str]:
    evidence = local_evidence if isinstance(local_evidence, dict) else {}
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
    if _bool(evidence, "strong_impact_like_motion"):
        reasons.append("strong_impact_like_motion")

    return reasons


def _extract_ai_evidence(ai_evidence: dict | None) -> dict:
    if not isinstance(ai_evidence, dict):
        return {}
    nested = ai_evidence.get("ai_evidence")
    if isinstance(nested, dict):
        return nested
    return ai_evidence


def _ai_recommendation(ai_evidence: dict | None) -> str:
    evidence = _extract_ai_evidence(ai_evidence)
    return _severity(
        evidence.get("recommended_severity")
        or evidence.get("recommendation")
        or evidence.get("severity")
    )


def choose_primary_camera(local_evidence: dict) -> str:
    evidence = local_evidence if isinstance(local_evidence, dict) else {}
    cameras = evidence.get("available_cameras")
    if not isinstance(cameras, list) or not cameras:
        return str(evidence.get("primary_camera_candidate") or "unknown")

    candidate = str(evidence.get("primary_camera_candidate") or "")
    if candidate in cameras:
        return candidate

    camera_evidence = evidence.get("camera_evidence") if isinstance(evidence.get("camera_evidence"), dict) else {}
    if camera_evidence:
        best_camera, best_score = "", -1.0
        for camera, details in camera_evidence.items():
            if camera not in cameras or not isinstance(details, dict):
                continue
            try:
                score = float(details.get("evidence_score") or 0.0)
            except (TypeError, ValueError):
                score = 0.0
            if score > best_score:
                best_camera, best_score = camera, score
        if best_camera and best_score > 0:
            return best_camera

    for camera in ["back", "rear", "front", *CAMERA_PRIORITY]:
        if camera in cameras:
            return camera

    return str(cameras[0])


def _base_event_type(evidence: dict) -> str:
    if _bool(evidence, "possible_impact") or _level(evidence, "impact_level") != "NONE":
        return "possible_impact"
    if _bool(evidence, "possible_contact") or _level(evidence, "contact_level") != "NONE":
        return "possible_contact"
    if _bool(evidence, "person_passby") or _bool(evidence, "person_passby_detected"):
        return "person_passby"
    if _bool(evidence, "person_near_only") or _bool(evidence, "person_detected"):
        return "person_near_vehicle"
    if _bool(evidence, "normal_traffic") or _bool(evidence, "normal_traffic_evidence"):
        return "normal_traffic"
    if _bool(evidence, "multi_camera"):
        return "multi_camera_event"
    return "single_camera_event"


def _summary_for(severity: str, event_type: str, evidence: dict) -> str:
    if severity == "IMPORTANT":
        return "Possible impact/contact evidence was detected."
    if event_type == "person_passby":
        return "People passed near the vehicle. No contact, impact, or tampering was detected."
    if event_type == "person_near_vehicle":
        return "Mimir saw a person near the vehicle, but no clear contact or tampering was detected."
    if event_type == "normal_traffic":
        return "Normal traffic or background movement was detected. No contact or impact evidence was found."
    if not _bool(evidence, "has_video"):
        return "Mimir found an event group, but no video file could be confirmed."
    if severity == "REVIEW":
        return "Mimir found uncertain activity worth review."
    return "No concerning evidence was found."


def resolve_severity(local_evidence: dict, ai_evidence: dict | None = None) -> dict:
    evidence = local_evidence if isinstance(local_evidence, dict) else {}
    ai = _extract_ai_evidence(ai_evidence)
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

    hard_reasons = important_evidence_reasons(evidence)
    hard_important = bool(hard_reasons)
    debug["important_evidence_found"] = hard_important
    debug["important_evidence_reasons"] = hard_reasons

    has_video = bool(evidence.get("has_video", True))
    severity = "IGNORE" if has_video else "REVIEW"
    event_type = _base_event_type(evidence)

    if not has_video:
        reasons.append("Video file could not be confirmed.")

    if _bool(evidence, "normal_traffic") or _bool(evidence, "normal_traffic_evidence"):
        severity = "IGNORE"
        reasons.append("Normal traffic evidence.")

    if _bool(evidence, "person_passby") or _bool(evidence, "person_passby_detected"):
        reasons.append("Brief person pass-by evidence.")
        if _bool(evidence, "uncertain"):
            severity = _max_severity(severity, "REVIEW")

    if _bool(evidence, "person_near_only"):
        severity = _max_severity(severity, "REVIEW")
        reasons.append("Person near vehicle without contact, impact, or tampering evidence.")

    if _bool(evidence, "possible_contact"):
        severity = _max_severity(severity, "REVIEW")
        reasons.append("Possible contact evidence.")

    if _bool(evidence, "possible_impact"):
        severity = _max_severity(severity, "REVIEW")
        reasons.append("Possible impact evidence.")

    if _bool(evidence, "uncertain"):
        severity = _max_severity(severity, "REVIEW")
        reasons.append("Uncertain local evidence.")

    if _bool(evidence, "crash_safety_triggered"):
        if SEVERITY_RANK[severity] < SEVERITY_RANK["REVIEW"]:
            debug["severity_floor_applied"] = True
            debug["severity_floor_reason"] = "crash_safety_triggered requires at least REVIEW"
        severity = _max_severity(severity, "REVIEW")
        reasons.append("Crash safety trigger.")

    if hard_important:
        severity = "IMPORTANT"
        reasons.extend(hard_reasons)

    ai_recommendation = _ai_recommendation(ai)
    if ai_recommendation == "IMPORTANT":
        if hard_important:
            severity = "IMPORTANT"
            reasons.append("AI supported by hard local evidence.")
        else:
            debug["ai_blocked_reason"] = "AI escalation blocked: no hard contact, impact, or tampering evidence."
            reasons.append(debug["ai_blocked_reason"])
    elif ai_recommendation == "REVIEW":
        if not (_bool(evidence, "normal_traffic") or _bool(evidence, "normal_traffic_evidence")) or hard_important:
            severity = _max_severity(severity, "REVIEW")
            reasons.append("AI recommended review.")

    cap_reason = ""
    if not hard_important:
        if _bool(evidence, "normal_traffic") or _bool(evidence, "normal_traffic_evidence"):
            cap_reason = "normal traffic capped at IGNORE without hard evidence"
            severity = _min_severity(severity, "IGNORE")
        elif _bool(evidence, "person_passby") or _bool(evidence, "person_passby_detected"):
            if _has_contact_impact_or_tampering(evidence):
                cap_reason = "person pass-by capped at REVIEW without hard evidence"
                severity = _min_severity(severity, "REVIEW")
            elif _bool(evidence, "uncertain"):
                cap_reason = "person pass-by capped at REVIEW because evidence is uncertain"
                severity = _min_severity(severity, "REVIEW")
            else:
                cap_reason = "person pass-by capped at IGNORE without contact, impact, or tampering"
                severity = _min_severity(severity, "IGNORE")
        elif _bool(evidence, "person_near_only"):
            cap_reason = "person near only capped at REVIEW without contact, impact, or tampering"
            severity = _min_severity(severity, "REVIEW")
        elif _bool(evidence, "possible_contact") and _level(evidence, "contact_level") in {"NONE", "LOW", "MEDIUM"}:
            cap_reason = "weak possible contact capped at REVIEW without high contact or visible contact"
            severity = _min_severity(severity, "REVIEW")

    if cap_reason:
        debug["severity_cap_applied"] = True
        debug["severity_cap_reason"] = cap_reason
        reasons.append(cap_reason)

    summary = _summary_for(severity, event_type, evidence)
    return {
        "severity": severity,
        "final_severity": severity,
        "event_type": event_type,
        "summary": summary,
        "primary_camera": choose_primary_camera(evidence),
        "severity_reasons": reasons or ["No concerning evidence found."],
        "classification_debug": debug,
    }

