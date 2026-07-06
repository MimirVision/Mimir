"""Guardrail tests for controlled Core v2 AI review."""

from __future__ import annotations

from .ai_reviewer import parse_ai_json, review_event_group, should_review_with_ai
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


def test_invalid_ai_json_is_safe() -> tuple[bool, str]:
    parsed, parse_error = parse_ai_json("not json at all")
    result = resolve_severity({"has_video": True, "normal_traffic": True}, {"ai_evidence": parsed})
    return _case(
        "Invalid AI JSON is safe",
        parse_error and parsed == {} and result.get("final_severity") == "IGNORE",
        f"parse_error={parse_error}, parsed={parsed}, final={result.get('final_severity')}",
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
        test_invalid_ai_json_is_safe(),
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
