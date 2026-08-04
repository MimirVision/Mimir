"""Unit-like tests for the Core v2 severity resolver."""

from __future__ import annotations

from .severity_resolver import SEVERITY_RANK, resolve_severity


def run_case(
    name: str,
    evidence: dict,
    ai_evidence: dict | None,
    allowed: set[str],
    blocked_ai_required: bool = False,
) -> tuple[bool, str]:
    merged = {"has_video": True, "multi_camera": False, **evidence}
    result = resolve_severity(merged, ai_evidence or {})
    actual = result.get("final_severity")
    debug = result.get("classification_debug") if isinstance(result.get("classification_debug"), dict) else {}
    ok = actual in allowed
    if blocked_ai_required and not debug.get("ai_blocked_reason"):
        ok = False
    detail = f"{name}: expected {sorted(allowed)}, actual {actual}"
    if blocked_ai_required:
        detail += f", ai_blocked_reason={debug.get('ai_blocked_reason', '')!r}"
    return ok, detail


def at_least_review(name: str, evidence: dict) -> tuple[bool, str]:
    result = resolve_severity({"has_video": True, **evidence}, {})
    actual = result.get("final_severity")
    ok = actual in SEVERITY_RANK and SEVERITY_RANK[actual] >= SEVERITY_RANK["REVIEW"]
    return ok, f"{name}: expected at least REVIEW, actual {actual}"


def main() -> int:
    cases = [
        run_case(
            "person_passby true, no contact/impact/tampering",
            {"person_passby": True},
            {},
            {"IGNORE"},
        ),
        run_case(
            "person_passby + low motion",
            {
                "person_passby": True,
                "person_passby_detected": True,
                "motion_score": 0.02,
                "max_motion_score": 0.08,
                "impact_level": "NONE",
                "contact_level": "NONE",
                "possible_impact": False,
                "possible_contact": False,
            },
            {},
            {"IGNORE"},
        ),
        run_case(
            "person_passby + strong_impact_like_motion",
            {"person_passby": True, "strong_impact_like_motion": True},
            {},
            {"IMPORTANT"},
        ),
        run_case(
            "person_passby + hard_contact_candidate",
            {"person_passby": True, "hard_contact_candidate": True},
            {},
            {"IMPORTANT"},
        ),
        run_case(
            "person_near_only true, no contact/impact/tampering",
            {"person_near_only": True},
            {},
            {"IGNORE", "REVIEW"},
        ),
        run_case(
            "normal_traffic true, no contact/impact",
            {"normal_traffic": True},
            {},
            {"IGNORE"},
        ),
        run_case(
            "possible_contact true, contact_level LOW",
            {"possible_contact": True, "contact_level": "LOW"},
            {},
            {"REVIEW"},
        ),
        run_case(
            "possible_contact true, contact_level MEDIUM",
            {"possible_contact": True, "contact_level": "MEDIUM"},
            {},
            {"REVIEW"},
        ),
        run_case(
            "impact_level MEDIUM",
            {"impact_level": "MEDIUM"},
            {},
            {"REVIEW"},
        ),
        run_case(
            "contact_level MEDIUM",
            {"contact_level": "MEDIUM"},
            {},
            {"REVIEW"},
        ),
        run_case(
            "contact_level MEDIUM only",
            {"contact_level": "MEDIUM", "possible_contact": True},
            {},
            {"REVIEW"},
        ),
        run_case(
            "hard_contact_candidate true",
            {"hard_contact_candidate": True},
            {},
            {"IMPORTANT"},
        ),
        run_case(
            "rear_impact_candidate true",
            {"rear_impact_candidate": True},
            {},
            {"IMPORTANT"},
        ),
        run_case(
            "strong_impact_like_motion true",
            {"strong_impact_like_motion": True},
            {},
            {"IMPORTANT"},
        ),
        run_case(
            "no_yolo_motion_impact_candidate true",
            {"object_detection_available": False, "no_yolo_motion_impact_candidate": True},
            {},
            {"IMPORTANT"},
        ),
        run_case(
            "crash_safety_triggered true",
            {"crash_safety_triggered": True},
            {},
            {"IMPORTANT"},
        ),
        run_case(
            "no-YOLO weak motion only",
            {
                "object_detection_available": False,
                "motion_score": 0.01,
                "max_motion_score": 0.07,
                "localized_motion_score": 0.08,
                "motion_spike_ratio": 1.2,
                "camera_shake_score": 0.01,
                "impact_level": "NONE",
                "contact_level": "NONE",
                "possible_impact": False,
                "possible_contact": False,
                "no_yolo_motion_impact_candidate": False,
                "crash_safety_triggered": False,
            },
            {},
            {"IGNORE"},
        ),
        run_case(
            "contact_level HIGH",
            {"contact_level": "HIGH"},
            {},
            {"IMPORTANT"},
        ),
        run_case(
            "impact_level HIGH",
            {"impact_level": "HIGH"},
            {},
            {"IMPORTANT"},
        ),
        # The following four reproduce a real false-positive report: three
        # SavedClips incidents (footage saved while driving, not parked
        # Sentry) all landed on IMPORTANT purely from ambient road
        # vibration/traffic, and the tester's feedback was "just normal
        # driving" for all three.
        run_case(
            "impact_level HIGH on SavedClips with no other signal is only ambient driving motion",
            {"impact_level": "HIGH", "source_category": "SavedClips"},
            {},
            {"REVIEW"},
        ),
        run_case(
            "contact_level HIGH on RecentClips with no other signal is only ambient driving motion",
            {"contact_level": "HIGH", "source_category": "RecentClips"},
            {},
            {"REVIEW"},
        ),
        run_case(
            "strong_impact_like_motion on SavedClips with no other signal is only ambient driving motion",
            {"strong_impact_like_motion": True, "source_category": "SavedClips"},
            {},
            {"REVIEW"},
        ),
        run_case(
            "impact_level HIGH on SavedClips still escalates when corroborated by hard_contact_candidate",
            {"impact_level": "HIGH", "source_category": "SavedClips", "hard_contact_candidate": True},
            {},
            {"IMPORTANT"},
        ),
        run_case(
            "impact_level HIGH on parked SentryClips is unaffected -- still IMPORTANT",
            {"impact_level": "HIGH", "source_category": "SentryClips"},
            {},
            {"IMPORTANT"},
        ),
        run_case(
            "AI recommends IMPORTANT but local evidence is person_passby only",
            {"person_passby": True},
            {"recommended_severity": "IMPORTANT"},
            {"IGNORE", "REVIEW"},
            blocked_ai_required=True,
        ),
        run_case(
            "visible_contact true",
            {"visible_contact": True},
            {},
            {"IMPORTANT"},
        ),
    ]

    failures = []
    print("Mimir Core v2 Resolver Tests")
    print("============================")
    for ok, detail in cases:
        print(("PASS " if ok else "FAIL ") + detail)
        if not ok:
            failures.append(detail)

    if failures:
        print()
        print("RESOLVER TESTS FAILED")
        return 1

    print()
    print("RESOLVER TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
