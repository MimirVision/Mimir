"""Evaluate an unpromoted Mimir candidate against the frozen baseline."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SEVERITIES = ("IGNORE", "REVIEW", "IMPORTANT")


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def severity(value: object) -> str:
    normalized = str(value or "").upper()
    return normalized if normalized in SEVERITIES else "IGNORE"


def items_by_id(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    values: list[Any] = []
    for key in ("items", "incidents", "predictions", "temporal_items", "sequences"):
        if isinstance(payload.get(key), list) and payload.get(key):
            values = payload[key]
            break
    return {
        str(item.get("incident_id") or item.get("id")): item
        for item in values
        if isinstance(item, dict) and (item.get("incident_id") or item.get("id"))
    }


def confusion(items: list[tuple[str, str]]) -> dict[str, dict[str, int]]:
    matrix = {truth: {predicted: 0 for predicted in SEVERITIES} for truth in SEVERITIES}
    for truth, predicted in items:
        matrix[truth][predicted] += 1
    return matrix


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))
    return ordered[index]


def evaluate_model(
    truth: dict[str, dict[str, Any]], predictions: dict[str, dict[str, Any]], label: str
) -> dict[str, Any]:
    pairs: list[tuple[str, str]] = []
    category_pairs: dict[str, list[tuple[str, str]]] = defaultdict(list)
    critical_total = critical_recalled = false_ignores = false_importants = 0
    door_total = door_recalled = 0
    normal_total = 0
    timing_errors: list[float] = []
    missing: list[str] = []
    for incident_id, expected in truth.items():
        prediction = predictions.get(incident_id)
        if prediction is None:
            missing.append(incident_id)
            continue
        expected_severity = severity(expected.get("human_severity") or expected.get("expected_severity"))
        predicted_severity = severity(prediction.get("severity") or prediction.get("final_severity"))
        pairs.append((expected_severity, predicted_severity))
        category = str(expected.get("category") or expected.get("contact_outcome") or "unknown")
        category_pairs[category].append((expected_severity, predicted_severity))
        outcome = str(expected.get("contact_outcome") or "").lower()
        is_critical = expected_severity == "IMPORTANT" or outcome == "impact"
        is_door = category in {"door_contact", "door_ding"} or bool(expected.get("vehicle_door_involved"))
        is_normal = expected_severity == "IGNORE" or outcome == "no_contact"
        if is_critical:
            critical_total += 1
            if predicted_severity == "IMPORTANT":
                critical_recalled += 1
            if predicted_severity == "IGNORE":
                false_ignores += 1
        if is_door:
            door_total += 1
            if predicted_severity in {"REVIEW", "IMPORTANT"}:
                door_recalled += 1
        if is_normal:
            normal_total += 1
            if predicted_severity == "IMPORTANT":
                false_importants += 1
        true_time = expected.get("impact_time_sec")
        if true_time is None:
            true_time = expected.get("apparent_contact_time_sec")
        predicted_time = prediction.get("best_time_sec")
        if predicted_time is None:
            predicted_time = prediction.get("primary_key_moment_sec")
        if true_time is not None and predicted_time is not None:
            timing_errors.append(abs(float(predicted_time) - float(true_time)))
    return {
        "name": label,
        "matched": len(pairs),
        "missing_predictions": missing,
        "confusion_matrix": confusion(pairs),
        "per_category_confusion_matrices": {
            category: confusion(values) for category, values in sorted(category_pairs.items())
        },
        "critical_total": critical_total,
        "critical_recall": critical_recalled / critical_total if critical_total else None,
        "false_ignores": false_ignores,
        "door_contact_total": door_total,
        "door_contact_recall": door_recalled / door_total if door_total else None,
        "normal_total": normal_total,
        "false_importants": false_importants,
        "false_important_rate": false_importants / normal_total if normal_total else None,
        "timing_error_distribution_sec": {
            "count": len(timing_errors),
            "median": statistics.median(timing_errors) if timing_errors else None,
            "p95": percentile(timing_errors, 0.95),
            "maximum": max(timing_errors) if timing_errors else None,
        },
    }


def source_hashes(payload: dict[str, Any]) -> set[str]:
    values = payload.get("items") or payload.get("temporal_items") or payload.get("sequences") or []
    return {
        str(item.get("source_group_hash"))
        for item in values
        if isinstance(item, dict) and item.get("source_group_hash")
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Locked Mimir candidate evaluation.")
    parser.add_argument("--locked-test", required=True)
    parser.add_argument("--baseline-predictions", required=True)
    parser.add_argument("--candidate-predictions", required=True)
    parser.add_argument("--candidate-manifest", required=True)
    parser.add_argument("--training-manifest", default="")
    parser.add_argument("--report", required=True)
    args = parser.parse_args(argv)
    try:
        test_path = Path(args.locked_test)
        test_payload = read_json(test_path)
        baseline_payload = read_json(Path(args.baseline_predictions))
        candidate_payload = read_json(Path(args.candidate_predictions))
        candidate_manifest = read_json(Path(args.candidate_manifest))
        if candidate_manifest.get("promoted") is not False:
            raise ValueError("Candidate manifest must remain explicitly unpromoted during evaluation.")
        truth = items_by_id(test_payload)
        baseline = evaluate_model(truth, items_by_id(baseline_payload), "free-beta-baseline-0.5.0")
        candidate = evaluate_model(truth, items_by_id(candidate_payload), str(candidate_manifest.get("candidate_id") or "candidate"))
        blockers: list[str] = []
        model_path = Path(str(candidate_manifest.get("model_path") or ""))
        if not model_path.is_absolute():
            model_path = Path(args.candidate_manifest).resolve().parent / model_path
        if not model_path.is_file() or sha256(model_path) != str(candidate_manifest.get("sha256") or ""):
            blockers.append("candidate model is missing or its checksum does not match the manifest")
        if candidate_payload.get("release_eligible") is not True:
            blockers.append("candidate predictions are not eligible for locked evaluation")
        if candidate_payload.get("candidate_manifest_sha256") != sha256(Path(args.candidate_manifest)):
            blockers.append("candidate predictions do not match the supplied candidate manifest")
        category_counts = Counter(
            str(item.get("contact_outcome") or item.get("category") or "unknown") for item in truth.values()
        )
        positives = sum(
            1 for item in truth.values() if str(item.get("contact_outcome") or "") in {"contact", "impact"}
        )
        hard_negatives = sum(
            1 for item in truth.values() if str(item.get("contact_outcome") or "") == "no_contact"
        )
        if len(truth) < 750:
            blockers.append(f"locked test has {len(truth)} groups; external beta gate requires 750")
        if positives < 300:
            blockers.append(f"locked test has {positives} positives; gate requires 300")
        if hard_negatives < 300:
            blockers.append(f"locked test has {hard_negatives} hard negatives; gate requires 300")
        if candidate["missing_predictions"]:
            blockers.append(f"candidate is missing {len(candidate['missing_predictions'])} predictions")
        if baseline["missing_predictions"]:
            blockers.append(f"baseline is missing {len(baseline['missing_predictions'])} predictions")
        if candidate["critical_recall"] is None or candidate["critical_recall"] < 0.99:
            blockers.append("critical impact recall is below 99%")
        if candidate["false_ignores"] != 0:
            blockers.append(f"candidate has {candidate['false_ignores']} critical false Ignore results")
        if candidate["door_contact_recall"] is None or candidate["door_contact_recall"] < 0.97:
            blockers.append("door-contact recall is below 97%")
        false_rate = candidate["false_important_rate"]
        if false_rate is None or false_rate > 0.005:
            blockers.append("false Important rate is above 0.5% or cannot be measured")
        timing = candidate["timing_error_distribution_sec"]
        if timing["median"] is None or timing["median"] > 0.5:
            blockers.append("key-moment median error is above 0.5 seconds or cannot be measured")
        if timing["p95"] is None or timing["p95"] > 1.0:
            blockers.append("key-moment p95 error is above 1 second or cannot be measured")
        if baseline["critical_recall"] is not None and candidate["critical_recall"] is not None:
            if candidate["critical_recall"] < baseline["critical_recall"]:
                blockers.append("candidate critical recall is worse than the frozen baseline")
        if candidate["false_importants"] > baseline["false_importants"]:
            blockers.append("candidate creates more false Important results than the frozen baseline")
        overlap: list[str] = []
        if args.training_manifest:
            training_payload = read_json(Path(args.training_manifest))
            overlap = sorted(source_hashes(test_payload) & source_hashes(training_payload))
            if overlap:
                blockers.append(f"source leakage detected for {len(overlap)} source groups")
        report = {
            "schema_version": "mimir_evaluation_report_v1",
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "locked_test_manifest": str(test_path.resolve()),
            "locked_test_manifest_sha256": sha256(test_path),
            "locked_test_counts": {
                "groups": len(truth),
                "positives": positives,
                "hard_negatives": hard_negatives,
                "categories": dict(category_counts),
            },
            "candidate_manifest_sha256": sha256(Path(args.candidate_manifest)),
            "baseline": baseline,
            "candidate": candidate,
            "per_category_confusion_matrices": candidate["per_category_confusion_matrices"],
            "timing_error_distribution_sec": timing,
            "source_leakage": overlap,
            "promotion_gates": {
                "critical_impact_recall_min": 0.99,
                "critical_false_ignore_max": 0,
                "door_contact_recall_min": 0.97,
                "false_important_rate_max": 0.005,
                "key_moment_median_error_sec_max": 0.5,
                "key_moment_p95_error_sec_max": 1.0,
                "locked_test_groups_min": 750,
            },
            "promotion_allowed": not blockers,
            "blockers": blockers,
        }
        output = Path(args.report)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"Evaluation report: {output}")
        print("MODEL PROMOTION ALLOWED" if not blockers else "MODEL PROMOTION BLOCKED")
        for blocker in blockers:
            print(f"- {blocker}")
        return 0 if not blockers else 2
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Evaluation error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
