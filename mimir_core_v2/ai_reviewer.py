"""Safe structured AI evidence review for Core v2.

AI review is supporting evidence only. The severity resolver owns the final
decision and blocks unsafe AI escalation.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request


AI_REVIEW_VERSION = "controlled_ai_review_v2_0_1"
DEFAULT_AI_TIMEOUT_SEC = 45

AI_SCHEMA_DEFAULT = {
    "scene_type": "unclear",
    "visible_person": False,
    "visible_vehicle_close": False,
    "visible_contact": False,
    "visible_impact": False,
    "normal_passing_traffic": False,
    "person_interaction": False,
    "tampering": False,
    "door_handle_attempt": False,
    "recommended_severity": "IGNORE",
    "confidence": 0.0,
    "evidence": [],
    "concerns": [],
}

VALID_SCENE_TYPES = {
    "normal_traffic",
    "person_passby",
    "person_near_vehicle",
    "possible_contact",
    "possible_impact",
    "tampering",
    "unclear",
}

VALID_SEVERITIES = {"IGNORE", "REVIEW", "IMPORTANT"}


def empty_ai_review(model: str = "", skipped_reason: str = "") -> dict:
    return {
        "enabled": bool(model),
        "model": model,
        "ai_model": model,
        "ai_review_version": AI_REVIEW_VERSION,
        "ai_evidence": {},
        "ai_raw_response": "",
        "ai_parse_error": False,
        "ai_reviewed": False,
        "ai_review_skipped_reason": skipped_reason,
        "runtime_sec": 0.0,
        "recommended_severity": "IGNORE",
        "recommendation": "IGNORE",
        "summary": skipped_reason or "AI review was not requested for this v2 scan.",
    }


def _level(evidence: dict, field: str) -> str:
    return str(evidence.get(field) or "NONE").strip().upper()


def should_review_with_ai(evidence: dict, debug_review_all: bool = False) -> tuple[bool, str]:
    if not isinstance(evidence, dict):
        return False, "local evidence missing"
    if debug_review_all:
        return True, "debug review-all enabled"

    obvious_normal = bool(evidence.get("normal_traffic") or evidence.get("normal_traffic_evidence"))
    obvious_passby = bool(evidence.get("person_passby") or evidence.get("person_passby_detected"))
    impact_level = _level(evidence, "impact_level")
    contact_level = _level(evidence, "contact_level")

    candidate = any(
        bool(evidence.get(field))
        for field in (
            "possible_impact",
            "possible_contact",
            "person_lingering_detected",
            "vehicle_lingering_detected",
            "strong_motion_spike",
            "strong_impact_like_motion",
            "crash_safety_triggered",
            "unclear_activity",
            "uncertain",
        )
    )
    candidate = candidate or impact_level in {"MEDIUM", "HIGH"}
    candidate = candidate or contact_level in {"MEDIUM", "HIGH"}

    if (obvious_normal or obvious_passby) and not candidate:
        return False, "obvious normal traffic or pass-by; AI review skipped"

    if candidate:
        return True, ""

    if evidence.get("person_detected") or evidence.get("vehicle_detected"):
        return True, ""

    return False, "no uncertain or concerning local evidence"


def build_prompt(event_group: dict, evidence: dict) -> str:
    prompt_package = {
        "event_group_id": event_group.get("event_group_id", ""),
        "available_cameras": event_group.get("available_cameras", []),
        "primary_camera": evidence.get("primary_camera_candidate") or "",
        "local_evidence": {
            key: evidence.get(key)
            for key in (
                "motion_score",
                "max_motion_score",
                "motion_spike_time_sec",
                "scene_change_score",
                "possible_impact",
                "impact_level",
                "possible_contact",
                "contact_level",
                "person_detected",
                "vehicle_detected",
                "person_passby_detected",
                "person_lingering_detected",
                "vehicle_passby_detected",
                "vehicle_lingering_detected",
                "normal_traffic_evidence",
                "person_near_only",
                "visible_contact",
                "visible_impact",
                "person_interaction_evidence",
                "tampering_evidence",
                "door_handle_attempt",
                "crash_safety_triggered",
                "uncertain",
            )
        },
        "available_visual_artifacts": {
            "hero_thumbnail": evidence.get("hero_thumbnail", ""),
            "contact_sheet": evidence.get("contact_sheet", ""),
        },
    }

    return (
        "You are reviewing one grouped vehicle-camera event for Project Mimir. "
        "Return only valid JSON matching this schema:\n"
        "{\n"
        '  "scene_type": "normal_traffic|person_passby|person_near_vehicle|possible_contact|possible_impact|tampering|unclear",\n'
        '  "visible_person": true,\n'
        '  "visible_vehicle_close": false,\n'
        '  "visible_contact": false,\n'
        '  "visible_impact": false,\n'
        '  "normal_passing_traffic": true,\n'
        '  "person_interaction": false,\n'
        '  "tampering": false,\n'
        '  "door_handle_attempt": false,\n'
        '  "recommended_severity": "IGNORE|REVIEW|IMPORTANT",\n'
        '  "confidence": 0.0,\n'
        '  "evidence": [],\n'
        '  "concerns": []\n'
        "}\n"
        "Do not invent contact, impact, tampering, or door interaction. "
        "If evidence only suggests ordinary traffic or pass-by activity, recommend IGNORE or REVIEW.\n\n"
        f"Event evidence package:\n{json.dumps(prompt_package, indent=2)}"
    )


def parse_ai_json(raw_response: str) -> tuple[dict, bool]:
    text = str(raw_response or "").strip()
    if not text:
        return {}, True

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return {}, True
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return {}, True

    if not isinstance(parsed, dict):
        return {}, True

    normalized = dict(AI_SCHEMA_DEFAULT)
    normalized.update(parsed)
    if normalized["scene_type"] not in VALID_SCENE_TYPES:
        normalized["scene_type"] = "unclear"
    normalized["recommended_severity"] = str(normalized.get("recommended_severity") or "IGNORE").upper()
    if normalized["recommended_severity"] not in VALID_SEVERITIES:
        normalized["recommended_severity"] = "IGNORE"
    for field in (
        "visible_person",
        "visible_vehicle_close",
        "visible_contact",
        "visible_impact",
        "normal_passing_traffic",
        "person_interaction",
        "tampering",
        "door_handle_attempt",
    ):
        normalized[field] = bool(normalized.get(field))
    try:
        normalized["confidence"] = max(0.0, min(1.0, float(normalized.get("confidence") or 0.0)))
    except (TypeError, ValueError):
        normalized["confidence"] = 0.0
    if not isinstance(normalized.get("evidence"), list):
        normalized["evidence"] = []
    if not isinstance(normalized.get("concerns"), list):
        normalized["concerns"] = []

    return normalized, False


def call_ollama(model: str, prompt: str, timeout_sec: int = 60) -> str:
    payload = json.dumps(
        {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        "http://127.0.0.1:11434/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_sec) as response:
        data = json.loads(response.read().decode("utf-8", errors="replace"))
    return str(data.get("response") or "")

def review_event_group(
    event_group: dict,
    evidence: dict,
    vlm: str | None = None,
    timeout_sec: int = DEFAULT_AI_TIMEOUT_SEC,
    debug_review_all: bool = False,
) -> dict:
    if not vlm:
        return empty_ai_review("", "AI model not configured.")

    should_review, skipped_reason = should_review_with_ai(evidence, debug_review_all=debug_review_all)
    if not should_review:
        return empty_ai_review(vlm, skipped_reason)

    prompt = build_prompt(event_group, evidence)
    started = time.perf_counter()
    try:
        raw_response = call_ollama(vlm, prompt, timeout_sec=max(1, int(timeout_sec or DEFAULT_AI_TIMEOUT_SEC)))
    except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError, ValueError) as exc:
        review = empty_ai_review(vlm, f"AI review failed safely: {exc}")
        review["runtime_sec"] = round(time.perf_counter() - started, 3)
        return review

    parsed, parse_error = parse_ai_json(raw_response)
    return {
        "enabled": True,
        "model": vlm,
        "ai_model": vlm,
        "ai_review_version": AI_REVIEW_VERSION,
        "ai_evidence": parsed,
        "ai_raw_response": raw_response,
        "ai_parse_error": parse_error,
        "ai_reviewed": True,
        "ai_review_skipped_reason": "",
        "runtime_sec": round(time.perf_counter() - started, 3),
        "recommended_severity": parsed.get("recommended_severity", "IGNORE") if parsed else "IGNORE",
        "recommendation": parsed.get("recommended_severity", "IGNORE") if parsed else "IGNORE",
        "summary": "AI returned structured evidence." if not parse_error else "AI response was not valid JSON; local resolver continued.",
    }
