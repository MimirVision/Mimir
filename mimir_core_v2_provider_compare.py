"""Compare CPU and accelerated Core v2 sessions without changing detection."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DECISION_FIELDS = (
    "final_severity",
    "event_type",
    "primary_key_moment_sec",
    "primary_key_moment_label",
    "key_moments",
)
RUNTIME_KEYS = {
    "object_detector_runtime_sec",
    "object_detector_providers",
    "object_detector_cpu_threads",
    "detector_cache_hits",
    "detector_cache_misses",
    "detector_cache_writes",
    "analysis_cache_hits",
    "analysis_cache_misses",
    "analysis_cache_writes",
}


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def benchmark_summary(session_path: Path) -> dict[str, Any]:
    path = session_path.parent / "benchmark_report.json"
    return read_json(path) if path.is_file() else {}


def compare_values(left: Any, right: Any, path: str = "") -> tuple[list[dict], list[dict]]:
    semantic: list[dict] = []
    numeric: list[dict] = []
    if isinstance(left, dict) and isinstance(right, dict):
        for key in sorted(set(left) & set(right)):
            if key in RUNTIME_KEYS or key in {"source_video", "thumbnail", "hero_thumbnail", "contact_sheet"}:
                continue
            child_semantic, child_numeric = compare_values(left[key], right[key], f"{path}.{key}" if path else key)
            semantic.extend(child_semantic)
            numeric.extend(child_numeric)
    elif isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            semantic.append({"path": path, "cpu": len(left), "accelerated": len(right), "kind": "length"})
        for index, (left_item, right_item) in enumerate(zip(left, right)):
            child_semantic, child_numeric = compare_values(left_item, right_item, f"{path}[{index}]")
            semantic.extend(child_semantic)
            numeric.extend(child_numeric)
    elif isinstance(left, (int, float)) and not isinstance(left, bool) and isinstance(right, (int, float)) and not isinstance(right, bool):
        difference = abs(float(left) - float(right))
        if difference > 1e-8:
            numeric.append({"path": path, "absolute_difference": difference, "cpu": left, "accelerated": right})
    elif left != right:
        semantic.append({"path": path, "cpu": left, "accelerated": right, "kind": "value"})
    return semantic, numeric


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cpu-session", required=True)
    parser.add_argument("--accelerated-session", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--fixtures-complete", action="store_true")
    parser.add_argument("--numeric-tolerance", type=float, default=0.001)
    args = parser.parse_args()

    cpu_path = Path(args.cpu_session).resolve()
    accelerated_path = Path(args.accelerated_session).resolve()
    cpu = read_json(cpu_path)
    accelerated = read_json(accelerated_path)
    cpu_incidents = cpu.get("incidents") if isinstance(cpu.get("incidents"), list) else []
    accelerated_incidents = accelerated.get("incidents") if isinstance(accelerated.get("incidents"), list) else []
    decision_differences: list[dict] = []
    semantic_differences: list[dict] = []
    numeric_differences: list[dict] = []
    for cpu_incident, accelerated_incident in zip(cpu_incidents, accelerated_incidents):
        incident_id = str(cpu_incident.get("id") or "")
        fields = [field for field in DECISION_FIELDS if cpu_incident.get(field) != accelerated_incident.get(field)]
        if fields:
            decision_differences.append({"incident_id": incident_id, "fields": fields})
        semantic, numeric = compare_values(
            cpu_incident.get("local_evidence", {}),
            accelerated_incident.get("local_evidence", {}),
        )
        semantic_differences.extend({"incident_id": incident_id, **item} for item in semantic)
        numeric_differences.extend({"incident_id": incident_id, **item} for item in numeric)

    cpu_benchmark = benchmark_summary(cpu_path)
    accelerated_benchmark = benchmark_summary(accelerated_path)
    accelerated_provider = str((accelerated.get("evidence_debug", {}).get("object_detector_providers") or [""])[0])
    max_numeric_difference = max(
        (float(item["absolute_difference"]) for item in numeric_differences),
        default=0.0,
    )
    benchmark_passed = all(
        int(accelerated_benchmark.get(field) or 0) == 0
        for field in ("failed", "critical_failures", "false_importants", "false_ignores")
    ) and int(accelerated_benchmark.get("labels_matched") or 0) > 0
    parity_passed = (
        len(cpu_incidents) == len(accelerated_incidents)
        and not decision_differences
        and not semantic_differences
        and max_numeric_difference <= args.numeric_tolerance
        and benchmark_passed
    )
    promotion_eligible = parity_passed and args.fixtures_complete and accelerated_provider == "DmlExecutionProvider"
    report = {
        "schema_version": "mimir_provider_parity_report_v1",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "cpu_session": str(cpu_path),
        "accelerated_session": str(accelerated_path),
        "accelerated_provider": accelerated_provider,
        "fixtures_complete": args.fixtures_complete,
        "incident_count_equal": len(cpu_incidents) == len(accelerated_incidents),
        "decision_differences": decision_differences,
        "semantic_evidence_differences": semantic_differences,
        "numeric_difference_count": len(numeric_differences),
        "max_numeric_difference": max_numeric_difference,
        "numeric_tolerance": args.numeric_tolerance,
        "cpu_runtime_sec": cpu.get("performance", {}).get("local_results_ready_sec"),
        "accelerated_runtime_sec": accelerated.get("performance", {}).get("local_results_ready_sec"),
        "cpu_benchmark": cpu_benchmark,
        "accelerated_benchmark": accelerated_benchmark,
        "parity_passed": parity_passed,
        "promotion_eligible": promotion_eligible,
        "recommendation": "promote_accelerated_provider" if promotion_eligible else "candidate_only_complete_missing_release_evidence",
    }
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output)
    print("Mimir provider parity: " + ("PASS" if parity_passed else "FAIL"))
    print(f"accelerated provider: {accelerated_provider or 'unavailable'}")
    print(f"max numeric difference: {max_numeric_difference:.6f}")
    print(f"promotion eligible: {'yes' if promotion_eligible else 'no'}")
    print(f"report: {output}")
    return 0 if parity_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
