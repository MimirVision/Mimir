"""Safe structured AI evidence review for Core v2.

AI review is supporting evidence only. The severity resolver owns the final
decision and blocks unsafe AI escalation.
"""

from __future__ import annotations

import json
import base64
import shutil
import time
import urllib.error
import urllib.request
from pathlib import Path


AI_REVIEW_VERSION = "controlled_ai_review_v2_0_1"
AI_PROMPT_VERSION = "parked_car_security_prompt_v2_0_2"
DEFAULT_AI_TIMEOUT_SEC = 45
AI_LOCAL_EVIDENCE_KEYS = (
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
    "hard_contact_candidate",
    "rear_impact_candidate",
    "vehicle_detected",
    "person_detected",
    "person_passby",
    "person_passby_detected",
    "person_near_only",
    "normal_traffic",
    "normal_traffic_evidence",
)
QUALITY_REPORT_VERSION = "ai_quality_report_v2_0_1"

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
        "ai_prompt_version": AI_PROMPT_VERSION,
        "ai_evidence": {},
        "ai_raw_response": "",
        "ai_parse_error": False,
        "ai_reviewed": False,
        "ai_review_failed": False,
        "ai_review_skipped_reason": skipped_reason,
        "ai_debug_dir": "",
        "ai_images_used": [],
        "ai_image_count": 0,
        "runtime_sec": 0.0,
        "recommended_severity": "IGNORE",
        "recommendation": "IGNORE",
        "summary": skipped_reason or "AI review was not requested for this v2 scan.",
    }


def _level(evidence: dict, field: str) -> str:
    return str(evidence.get(field) or "NONE").strip().upper()


def should_review_with_ai(evidence: dict, local_severity: str = "", debug_review_all: bool = False) -> tuple[bool, str]:
    if not isinstance(evidence, dict):
        return False, "local evidence missing"
    if debug_review_all:
        return True, "debug review-all enabled"

    impact_level = _level(evidence, "impact_level")
    contact_level = _level(evidence, "contact_level")
    severity = str(local_severity or "").strip().upper()

    candidate = any(
        bool(evidence.get(field))
        for field in (
            "possible_impact",
            "possible_contact",
            "strong_impact_like_motion",
            "hard_contact_candidate",
            "rear_impact_candidate",
        )
    )
    candidate = candidate or severity == "REVIEW"
    candidate = candidate or impact_level in {"MEDIUM", "HIGH"}
    candidate = candidate or contact_level in {"MEDIUM", "HIGH"}

    if candidate:
        return True, ""

    return False, "local decision did not need AI review"


def _image_path(value: object) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    path = Path(text)
    if not path.is_file():
        return None
    if path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
        return None
    return path


def build_visual_package(evidence: dict) -> tuple[list[str], list[dict]]:
    images: list[str] = []
    artifacts: list[dict] = []
    seen: set[str] = set()

    for field, label in (("contact_sheet", "contact_sheet"), ("hero_thumbnail", "hero_thumbnail")):
        path = _image_path(evidence.get(field))
        if not path:
            continue
        resolved = str(path.resolve()).lower()
        if resolved in seen:
            continue
        try:
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        except OSError:
            continue
        seen.add(resolved)
        images.append(encoded)
        artifacts.append({"type": label, "filename": path.name, "source_path": str(path)})

    return images, artifacts


def _local_evidence_summary(evidence: dict) -> dict:
    source = evidence if isinstance(evidence, dict) else {}
    return {key: source.get(key) for key in AI_LOCAL_EVIDENCE_KEYS if key in source}


def build_prompt(event_group: dict, evidence: dict, visual_artifacts: list[dict] | None = None) -> str:
    prompt_package = {
        "event_group_id": event_group.get("event_group_id", ""),
        "available_cameras": event_group.get("available_cameras", []),
        "primary_camera": evidence.get("primary_camera_candidate") or "",
        "local_evidence": _local_evidence_summary(evidence),
        "available_visual_artifacts": {
            "images": [
                {"type": item.get("type"), "filename": item.get("filename")}
                for item in (visual_artifacts or [])
            ],
        },
    }

    return (
        "You are reviewing parked-car/security footage for Project Mimir. "
        "The camera belongs to the parked POV vehicle. Your task is to detect contact, impact, tampering, "
        "door ding, rear-end collision, or suspicious interaction with the parked camera car. "
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
        "The attached images are a compact visual package, not the full video. "
        "Look carefully for: a vehicle contacting the parked camera car; a door opening into the car; "
        "a person touching the car or trying handles; a sudden jolt/contact moment; and before/after changes around the key moment. "
        "Do not dismiss the scene as normal traffic if local evidence indicates possible impact/contact. "
        "If uncertain, choose REVIEW, not IGNORE. "
        "Only choose IGNORE when there is clearly no contact, no impact, and no tampering. "
        "Do not invent contact, impact, tampering, or door interaction.\n\n"
        f"Event evidence package:\n{json.dumps(prompt_package, indent=2)}"
    )


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def _copy_debug_images(debug_dir: Path, visual_artifacts: list[dict]) -> list[dict]:
    copied: list[dict] = []
    image_dir = debug_dir / "images"
    image_dir.mkdir(parents=True, exist_ok=True)

    for index, artifact in enumerate(visual_artifacts, start=1):
        source_path = Path(str(artifact.get("source_path") or ""))
        if not source_path.is_file():
            continue
        destination = image_dir / f"{index:02d}_{artifact.get('type') or 'image'}_{source_path.name}"
        try:
            shutil.copy2(source_path, destination)
        except OSError:
            continue
        copied_artifact = dict(artifact)
        copied_artifact["copied_path"] = str(destination)
        copied.append(copied_artifact)

    return copied


def build_review_input(
    incident_id: str,
    event_group: dict,
    evidence: dict,
    local_decision: dict | None,
    visual_artifacts: list[dict],
    model: str,
    timeout_sec: int,
) -> dict:
    local = local_decision if isinstance(local_decision, dict) else {}
    return {
        "incident_id": incident_id,
        "event_group_id": event_group.get("event_group_id", ""),
        "local_final_severity_before_ai": local.get("final_severity") or local.get("severity") or "",
        "event_type": local.get("event_type", ""),
        "summary": local.get("summary", ""),
        "local_evidence": _local_evidence_summary(evidence),
        "image_paths_sent_to_ai": [item.get("source_path", "") for item in visual_artifacts],
        "image_count": len(visual_artifacts),
        "model": model,
        "timeout_sec": timeout_sec,
        "prompt_version": AI_PROMPT_VERSION,
    }


def write_ai_debug_package(
    debug_dir: str | Path | None,
    prompt: str,
    raw_response: str,
    parsed_response: dict,
    review_input: dict,
    local_evidence: dict,
    visual_artifacts: list[dict],
) -> tuple[str, list[dict]]:
    if not debug_dir:
        return "", visual_artifacts

    debug_path = Path(debug_dir)
    copied_artifacts = _copy_debug_images(debug_path, visual_artifacts)
    review_input_with_copies = dict(review_input)
    review_input_with_copies["copied_image_paths"] = [item.get("copied_path", "") for item in copied_artifacts]

    try:
        _write_text(debug_path / "prompt.txt", prompt)
        _write_text(debug_path / "raw_response.txt", raw_response)
        _write_json(debug_path / "parsed_response.json", parsed_response)
        _write_json(debug_path / "review_input.json", review_input_with_copies)
        _write_json(debug_path / "local_evidence.json", local_evidence)
    except OSError:
        return str(debug_path), copied_artifacts or visual_artifacts

    return str(debug_path), copied_artifacts or visual_artifacts


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


def call_ollama(model: str, prompt: str, images: list[str] | None = None, timeout_sec: int = 60) -> str:
    payload = json.dumps(
        {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "images": images or [],
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
    local_severity: str = "",
    local_decision: dict | None = None,
    incident_id: str = "",
    debug_dir: str | Path | None = None,
    timeout_sec: int = DEFAULT_AI_TIMEOUT_SEC,
    debug_review_all: bool = False,
) -> dict:
    if not vlm:
        return empty_ai_review("", "AI model not configured.")

    should_review, skipped_reason = should_review_with_ai(evidence, local_severity=local_severity, debug_review_all=debug_review_all)
    if not should_review:
        return empty_ai_review(vlm, skipped_reason)

    images, visual_artifacts = build_visual_package(evidence)
    prompt = build_prompt(event_group, evidence, visual_artifacts)
    safe_timeout = max(1, int(timeout_sec or DEFAULT_AI_TIMEOUT_SEC))
    review_input = build_review_input(
        incident_id=incident_id,
        event_group=event_group,
        evidence=evidence,
        local_decision=local_decision,
        visual_artifacts=visual_artifacts,
        model=vlm,
        timeout_sec=safe_timeout,
    )
    started = time.perf_counter()
    raw_response = ""
    parsed: dict = {}
    parse_error = False
    debug_path = ""
    debug_artifacts = visual_artifacts
    try:
        raw_response = call_ollama(vlm, prompt, images=images, timeout_sec=safe_timeout)
    except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError, ValueError) as exc:
        debug_path, debug_artifacts = write_ai_debug_package(
            debug_dir,
            prompt,
            f"AI review failed safely: {exc}",
            {},
            review_input,
            _local_evidence_summary(evidence),
            visual_artifacts,
        )
        review = empty_ai_review(vlm, f"AI review failed safely: {exc}")
        review["ai_review_failed"] = True
        review["runtime_sec"] = round(time.perf_counter() - started, 3)
        review["ai_debug_dir"] = debug_path
        review["ai_prompt_version"] = AI_PROMPT_VERSION
        review["ai_images_used"] = debug_artifacts
        review["ai_image_count"] = len(debug_artifacts)
        review["visual_artifacts"] = debug_artifacts
        review["local_final_severity_before_ai"] = review_input.get("local_final_severity_before_ai", "")
        return review

    parsed, parse_error = parse_ai_json(raw_response)
    debug_path, debug_artifacts = write_ai_debug_package(
        debug_dir,
        prompt,
        raw_response,
        parsed,
        review_input,
        _local_evidence_summary(evidence),
        visual_artifacts,
    )
    return {
        "enabled": True,
        "model": vlm,
        "ai_model": vlm,
        "ai_review_version": AI_REVIEW_VERSION,
        "ai_prompt_version": AI_PROMPT_VERSION,
        "ai_evidence": parsed,
        "ai_raw_response": raw_response,
        "ai_parse_error": parse_error,
        "ai_reviewed": True,
        "ai_review_failed": bool(parse_error),
        "ai_review_skipped_reason": "",
        "ai_debug_dir": debug_path,
        "runtime_sec": round(time.perf_counter() - started, 3),
        "visual_artifacts": debug_artifacts,
        "ai_images_used": debug_artifacts,
        "ai_image_count": len(debug_artifacts),
        "local_final_severity_before_ai": review_input.get("local_final_severity_before_ai", ""),
        "recommended_severity": parsed.get("recommended_severity", "IGNORE") if parsed else "IGNORE",
        "recommendation": parsed.get("recommended_severity", "IGNORE") if parsed else "IGNORE",
        "summary": "AI returned structured evidence." if not parse_error else "AI response was not valid JSON; local resolver continued.",
    }


def _severity(value: object) -> str:
    text = str(value or "").strip().upper()
    return text if text in VALID_SEVERITIES else ""


def _ai_evidence_from_incident(incident: dict) -> dict:
    evidence = incident.get("ai_evidence")
    return evidence if isinstance(evidence, dict) else {}


def _local_evidence_from_incident(incident: dict) -> dict:
    evidence = incident.get("local_evidence")
    return evidence if isinstance(evidence, dict) else {}


def _hard_local_important(evidence: dict) -> bool:
    return bool(
        evidence.get("hard_contact_candidate")
        or evidence.get("rear_impact_candidate")
        or evidence.get("strong_impact_like_motion")
        or _level(evidence, "impact_level") == "HIGH"
        or _level(evidence, "contact_level") == "HIGH"
    )


def _ai_review_meta(incident: dict) -> dict:
    review = incident.get("ai_evidence_review")
    return review if isinstance(review, dict) else {}


def ai_quality_flags_for_incident(incident: dict) -> dict:
    local = _local_evidence_from_incident(incident)
    ai = _ai_evidence_from_incident(incident)
    review = _ai_review_meta(incident)
    local_severity = _severity(review.get("local_final_severity_before_ai") or incident.get("final_severity"))
    ai_severity = _severity(ai.get("recommended_severity") or review.get("recommended_severity"))
    scene_type = str(ai.get("scene_type") or "").strip().lower()
    hard_local = _hard_local_important(local)
    candidate = bool(
        hard_local
        or local.get("possible_contact")
        or local.get("possible_impact")
        or _level(local, "impact_level") in {"MEDIUM", "HIGH"}
        or _level(local, "contact_level") in {"MEDIUM", "HIGH"}
    )

    reviewed_or_attempted = bool(incident.get("ai_reviewed") or review.get("ai_debug_dir") or review.get("ai_review_failed"))
    disagreed = bool(reviewed_or_attempted and local_severity and ai_severity and local_severity != ai_severity)
    downgraded_hard = bool(hard_local and local_severity == "IMPORTANT" and ai_severity in {"IGNORE", "REVIEW"})
    called_normal = bool(hard_local and scene_type == "normal_traffic")
    called_ignore = bool(hard_local and ai_severity == "IGNORE")
    false_ignore_candidate = bool(candidate and ai_severity == "IGNORE")

    return {
        "ai_disagreed_with_local_severity": disagreed,
        "ai_downgraded_hard_local_important": downgraded_hard,
        "ai_called_hard_impact_normal": called_normal,
        "ai_called_hard_impact_ignore": called_ignore,
        "ai_false_ignore_candidate": false_ignore_candidate,
        "hard_local_important": hard_local,
        "local_final_severity_before_ai": local_severity,
        "ai_recommended_severity": ai_severity,
        "ai_scene_type": scene_type,
    }


def build_ai_quality_report(session: dict) -> dict:
    incidents = session.get("incidents") if isinstance(session.get("incidents"), list) else []
    rows: list[dict] = []
    for incident in incidents:
        if not isinstance(incident, dict):
            continue
        flags = ai_quality_flags_for_incident(incident)
        rows.append(
            {
                "id": incident.get("id", ""),
                "event_timestamp": incident.get("event_timestamp", ""),
                "final_severity": incident.get("final_severity", ""),
                "ai_reviewed": bool(incident.get("ai_reviewed")),
                "ai_parse_error": bool(incident.get("ai_parse_error")),
                "ai_debug_dir": incident.get("ai_debug_dir", ""),
                **flags,
            }
        )

    return {
        "report_version": QUALITY_REPORT_VERSION,
        "ai_review_version": AI_REVIEW_VERSION,
        "ai_prompt_version": AI_PROMPT_VERSION,
        "ai_model": session.get("ai_model", ""),
        "ai_reviewed_count": sum(1 for item in rows if item.get("ai_reviewed")),
        "ai_failed_count": int(session.get("ai_failed_groups") or 0),
        "ai_parse_error_count": sum(1 for item in rows if item.get("ai_parse_error")),
        "ai_disagreement_count": sum(1 for item in rows if item.get("ai_disagreed_with_local_severity")),
        "ai_downgraded_hard_local_important_count": sum(1 for item in rows if item.get("ai_downgraded_hard_local_important")),
        "ai_false_ignore_candidate_count": sum(1 for item in rows if item.get("ai_false_ignore_candidate")),
        "incidents": rows,
    }


def write_ai_quality_report(session: dict, output_dir: str | Path) -> Path:
    report = build_ai_quality_report(session)
    path = Path(output_dir) / "ai_quality_report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return path
