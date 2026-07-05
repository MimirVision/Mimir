"""Unit-like tests for the Core v2 severity resolver."""

from __future__ import annotations

from .severity_resolver import resolve_severity


BASE_GROUP = {
    "available_cameras": ["front"],
    "clips": [{"camera": "front", "path": "example.mp4", "exists": True}],
}


def run_case(name: str, evidence: dict, ai_review: dict | None, expected: set[str]) -> tuple[bool, str]:
    merged = {"has_video": True, "multi_camera": False, **evidence}
    result = resolve_severity(BASE_GROUP, merged, ai_review or {})
    actual = result.get("final_severity")
    ok = actual in expected
    detail = f"{name}: expected {sorted(expected)}, actual {actual}"
    return ok, detail


def main() -> int:
    cases = [
        (
            "person pass-by no contact",
            {"person_passby": True},
            {},
            {"IGNORE"},
        ),
        (
            "person near no contact",
            {"person_near_only": True},
            {},
            {"IGNORE", "REVIEW"},
        ),
        (
            "normal traffic",
            {"normal_traffic": True},
            {},
            {"IGNORE"},
        ),
        (
            "rear impact high",
            {"impact_level": "HIGH", "possible_impact": True},
            {},
            {"IMPORTANT"},
        ),
        (
            "weak possible contact",
            {"possible_contact": True, "contact_level": "LOW"},
            {},
            {"REVIEW"},
        ),
        (
            "high contact",
            {"possible_contact": True, "contact_level": "HIGH"},
            {},
            {"IMPORTANT"},
        ),
        (
            "AI says Important but pass-by only",
            {"person_passby": True},
            {"recommendation": "IMPORTANT"},
            {"IGNORE", "REVIEW"},
        ),
    ]

    failures = []
    print("Mimir Core v2 Resolver Tests")
    print("============================")
    for name, evidence, ai_review, expected in cases:
        ok, detail = run_case(name, evidence, ai_review, expected)
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

