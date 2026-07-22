"""Guardrail tests for controlled Core v2 AI review."""

from __future__ import annotations

from . import ai_reviewer
from .ai_reviewer import ai_quality_flags_for_incident, parse_ai_json, review_event_group, should_review_with_ai
from .cli import run_scan
from .severity_resolver import resolve_severity


def _case(name: str, ok: bool, detail: str) -> tuple[bool, str]:
    return ok, f"{name}: {detail}"


def test_ai_important_blocked_for_passby() -> tuple[bool, str]:
    result = resolve_severity(
        {"has_video": True, "person_passby": True},
        {"recommended_severity": "IMPORTANT", "confidence": 0.95},
    )
    final = result.get("final_severity")
    blocked = bool(result.get("classification_debug", {}).get("ai_blocked_reason"))
    return _case(
        "AI Important blocked for person pass-by",
        final != "IMPORTANT" and blocked,
        f"final={final}, blocked={blocked}",
    )


def test_ai_important_blocked_for_person_near_only() -> tuple[bool, str]:
    result = resolve_severity(
        {"has_video": True, "person_near_only": True, "person_detected": True},
        {"recommended_severity": "IMPORTANT", "confidence": 0.95},
    )
    final = result.get("final_severity")
    blocked = bool(result.get("classification_debug", {}).get("ai_blocked_reason"))
    return _case(
        "AI Important blocked for person near only",
        final != "IMPORTANT" and blocked,
        f"final={final}, blocked={blocked}",
    )


def test_ai_ignore_cannot_downgrade_high_impact() -> tuple[bool, str]:
    result = resolve_severity(
        {"has_video": True, "impact_level": "HIGH"},
        {"recommended_severity": "IGNORE", "confidence": 0.95},
    )
    final = result.get("final_severity")
    return _case(
        "AI Ignore cannot downgrade high impact",
        final == "IMPORTANT",
        f"final={final}",
    )


def test_ai_ignore_cannot_downgrade_hard_rear_impact() -> tuple[bool, str]:
    result = resolve_severity(
        {
            "has_video": True,
            "hard_contact_candidate": True,
            "rear_impact_candidate": True,
            "impact_level": "HIGH",
            "contact_level": "HIGH",
        },
        {"recommended_severity": "IGNORE", "scene_type": "normal_traffic", "confidence": 0.9},
    )
    final = result.get("final_severity")
    return _case(
        "AI Ignore cannot downgrade hard rear-impact",
        final == "IMPORTANT",
        f"final={final}",
    )


def test_ai_normal_traffic_quality_flag_for_hard_impact() -> tuple[bool, str]:
    flags = ai_quality_flags_for_incident(
        {
            "id": "incident_test",
            "final_severity": "IMPORTANT",
            "local_evidence": {
                "hard_contact_candidate": True,
                "rear_impact_candidate": True,
                "impact_level": "HIGH",
                "contact_level": "HIGH",
            },
            "ai_reviewed": True,
            "ai_evidence": {
                "scene_type": "normal_traffic",
                "recommended_severity": "IGNORE",
            },
            "ai_evidence_review": {
                "local_final_severity_before_ai": "IMPORTANT",
            },
        }
    )
    return _case(
        "AI normal_traffic quality flag for hard impact",
        flags.get("ai_called_hard_impact_normal") is True
        and flags.get("ai_called_hard_impact_ignore") is True
        and flags.get("ai_downgraded_hard_local_important") is True,
        f"flags={flags}",
    )


def test_ai_visible_contact_floors_review() -> tuple[bool, str]:
    result = resolve_severity(
        {"has_video": True, "person_passby": True},
        {"visible_contact": True, "recommended_severity": "REVIEW", "confidence": 0.8},
    )
    final = result.get("final_severity")
    return _case(
        "AI visible_contact floors at REVIEW",
        final in {"REVIEW", "IMPORTANT"},
        f"final={final}",
    )


def test_ai_important_with_visible_contact_can_escalate() -> tuple[bool, str]:
    result = resolve_severity(
        {"has_video": True, "person_passby": True},
        {"visible_contact": True, "recommended_severity": "IMPORTANT", "confidence": 0.8},
    )
    final = result.get("final_severity")
    return _case(
        "AI Important with visible_contact can escalate",
        final == "IMPORTANT",
        f"final={final}",
    )


def test_invalid_ai_json_is_safe() -> tuple[bool, str]:
    parsed, parse_error = parse_ai_json("not json at all")
    result = resolve_severity({"has_video": True, "normal_traffic": True}, {"ai_evidence": parsed})
    return _case(
        "Invalid AI JSON is safe",
        parse_error and parsed == {} and result.get("final_severity") == "IGNORE",
        f"parse_error={parse_error}, parsed={parsed}, final={result.get('final_severity')}",
    )


def test_ai_unavailable_scan_completes() -> tuple[bool, str]:
    original_call_ollama = ai_reviewer.call_ollama

    def failing_call_ollama(*_args: object, **_kwargs: object) -> str:
        raise OSError("ollama unavailable for test")

    ai_reviewer.call_ollama = failing_call_ollama
    try:
        session = run_scan(
            r"C:\Mimir\Test",
            mode="balanced",
            vlm="unavailable-test-model",
            output=r"C:\Mimir_Backend\MimirOutputV2\ai_guardrail_test",
            ai_review_budget=1,
            ai_timeout_sec=1,
            ai_debug_review_all=True,
        )
    finally:
        ai_reviewer.call_ollama = original_call_ollama

    return _case(
        "AI unavailable scan completes",
        session.get("schema_version") == "mimir_v2" and int(session.get("incident_count") or 0) > 0,
        f"schema={session.get('schema_version')}, incidents={session.get('incident_count')}, failed={session.get('ai_failed_groups')}",
    )


def test_missing_ai_model_skips_safely() -> tuple[bool, str]:
    review = review_event_group(
        {"event_group_id": "test_group", "available_cameras": ["front"]},
        {"possible_contact": True},
        vlm="",
    )
    return _case(
        "Missing AI model skips safely",
        not review.get("ai_reviewed") and bool(review.get("ai_review_skipped_reason")),
        f"reviewed={review.get('ai_reviewed')}, reason={review.get('ai_review_skipped_reason')!r}",
    )


def test_obvious_passby_not_ai_candidate() -> tuple[bool, str]:
    should_review, reason = should_review_with_ai(
        {
            "person_passby": True,
            "person_passby_detected": True,
            "possible_contact": False,
            "possible_impact": False,
            "impact_level": "LOW",
            "contact_level": "NONE",
        }
    )
    debug_review, _ = should_review_with_ai({"person_passby": True}, debug_review_all=True)
    return _case(
        "Obvious pass-by is skipped unless debug review-all is enabled",
        not should_review and debug_review,
        f"should_review={should_review}, reason={reason!r}, debug_review={debug_review}",
    )


def main() -> int:
    cases = [
        test_ai_important_blocked_for_passby(),
        test_ai_important_blocked_for_person_near_only(),
        test_ai_ignore_cannot_downgrade_high_impact(),
        test_ai_ignore_cannot_downgrade_hard_rear_impact(),
        test_ai_normal_traffic_quality_flag_for_hard_impact(),
        test_ai_visible_contact_floors_review(),
        test_ai_important_with_visible_contact_can_escalate(),
        test_invalid_ai_json_is_safe(),
        test_ai_unavailable_scan_completes(),
        test_missing_ai_model_skips_safely(),
        test_obvious_passby_not_ai_candidate(),
    ]
    failures = []
    print("Mimir Core v2 AI Guardrail Tests")
    print("================================")
    for ok, detail in cases:
        print(("PASS " if ok else "FAIL ") + detail)
        if not ok:
            failures.append(detail)

    if failures:
        print()
        print("AI GUARDRAIL TESTS FAILED")
        return 1

    print()
    print("AI GUARDRAIL TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
