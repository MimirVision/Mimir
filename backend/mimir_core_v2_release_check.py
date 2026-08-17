"""Operational and external-release gates for Mimir Core v2.

The default mode is intentionally strict. Use ``--internal`` for dogfood builds;
external blockers remain in the report but do not hide operational test results.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
DEFAULT_FRONTEND_ROOT = ROOT.parent / "Mimir"
DEFAULT_OUTPUT_DIR = ROOT / "MimirOutputV2"
MIN_LOCKED_LABELS = 750
MIN_POSITIVE_LABELS = 300
MIN_HARD_NEGATIVES = 300

COMPILE_TARGETS = [
    "mimir_core_v2_scan.py",
    "mimir_core_v2_ai_enrich.py",
    "mimir_core_v2_actions.py",
    "mimir_core_v2_dataset.py",
    "mimir_core_v2_training.py",
    "mimir_core_v2_train_otw.py",
    "mimir_otw_data.py",
    "mimir_carla_data.py",
    "mimir_carla_generate.py",
    "mimir_carla_batch.py",
    "mimir_core_v2_train_carla.py",
    "mimir_core_v2_cvat.py",
    "mimir_core_v2_evaluate.py",
    "mimir_core_v2_reliability.py",
    "export_rfdetr_model.py",
    "mimir_core_v2/cli.py",
    "mimir_core_v2/progress.py",
    "mimir_core_v2/runtime_paths.py",
    "mimir_core_v2/onnx_object_detector.py",
    "mimir_core_v2/ego_vehicle.py",
    "mimir_core_v2/key_moment_refiner.py",
    "mimir_core_v2/event_grouping.py",
    "mimir_core_v2/frame_sampler.py",
    "mimir_core_v2/video_decode.py",
    "mimir_core_v2/detector_cache.py",
    "mimir_core_v2/motion_analysis.py",
    "mimir_core_v2/evidence_extractor.py",
    "mimir_core_v2/thumbnailer.py",
    "mimir_core_v2/ai_enrichment.py",
    "mimir_core_v2/severity_resolver.py",
    "mimir_core_v2/output_writer.py",
    "mimir_core_v2/cvat_client.py",
    "mimir_core_v2/dataset_package.py",
    "mimir_core_v2/temporal_training.py",
    "mimir_core_v2/candidate_perception.py",
    "mimir_core_v2/otw_auxiliary.py",
    "mimir_core_v2/carla_auxiliary.py",
    "mimir_core_v2/test_data_pipeline.py",
    "mimir_core_v2/test_grouping.py",
    "mimir_core_v2/test_evidence.py",
    "mimir_core_v2/test_contact_semantics.py",
    "mimir_core_v2/test_otw_auxiliary.py",
    "mimir_core_v2/test_carla_data.py",
    "mimir_core_v2/test_thumbnails.py",
    "mimir_core_v2/test_key_moments.py",
    "mimir_core_v2/test_resolver.py",
    "mimir_core_v2/test_ai_guardrails.py",
    "mimir_core_v2/test_release_foundation.py",
    "mimir_core_v2/test_storage_actions.py",
    "mimir_core_v2/test_onnx_detector.py",
    "mimir_core_v2/benchmark.py",
    "mimir_core_v2_audit.py",
    # model_manifest is on the critical path for the permissive_detector gate
    # and the real-footage smoke test, but was never compile-checked.
    "mimir_core_v2/model_manifest.py",
    "mimir_core_v2/model_update.py",
    "mimir_core_v2/validators.py",
    "mimir_core_v2/source_discovery.py",
    "mimir_core_v2/ai_reviewer.py",
    "mimir_core_v2_model_update.py",
    "mimir_core_v2/test_mode_invariance.py",
    "mimir_core_v2/test_dwell_flags.py",
    "mimir_core_v2/test_motion_baseline.py",
    "mimir_core_v2/test_selection_grid.py",
    "mimir_core_v2/test_model_update.py",
]

FORBIDDEN_TRACKED_PREFIXES = (
    ".venv/",
    "MimirOutput/",
    "MimirOutputV2/",
    "Frames/",
    "dist/",
    "dist_backend/",
    "build/",
    "build_backend/",
    "Test/",
    "beta-smoke-test/",
    "src-tauri/resources/mimir-backend/_internal/",
)
FORBIDDEN_TRACKED_SUFFIXES = (".mp4", ".mov", ".avi", ".pt", ".onnx")


def source_video_hashes(input_path: str | Path) -> dict[str, dict[str, Any]]:
    root = Path(input_path).resolve()
    if not root.is_dir():
        raise ReleaseCheckFailed(f"input folder missing: {root}")

    hashes: dict[str, dict[str, Any]] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() != ".mp4":
            continue
        hashes[str(path.relative_to(root))] = {
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
    return hashes


def sha256_file(path: Path) -> str:
    if not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ReleaseCheckFailed(Exception):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def frontend_package_version(frontend_root: Path) -> str:
    return str(read_json(frontend_root / "package.json").get("version") or "")


def command_text(args: list[str]) -> str:
    return " ".join(f'"{arg}"' if " " in str(arg) else str(arg) for arg in args)


def run_required(args: list[str], label: str) -> None:
    print(f"\n> {command_text(args)}")
    result = subprocess.run(args, cwd=ROOT)
    if result.returncode != 0:
        raise ReleaseCheckFailed(f"{label} failed with exit code {result.returncode}")


def add_check(checks: list[dict[str, Any]], name: str, passed: bool, detail: str, blocking: bool = True) -> None:
    checks.append({"name": name, "passed": bool(passed), "blocking": blocking, "detail": detail})


def repository_root(root: Path) -> Path | None:
    """The work-tree root of the repository containing `root`, if any."""

    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return Path(result.stdout.strip()).resolve()


def repository_files(root: Path) -> list[str] | None:
    """List files git knows about under `root`, or None if it is not part of this project.

    Two things this had to get right, and originally got wrong both ways.

    It used to test for a `.git` entry beside the folder. Backend and desktop
    were each their own repository; once they became folders in one, nothing sat
    beside either, so both hygiene checks degraded into the literal string
    "repository could not be inspected" and it was counted as two tracked
    violations. A blocking failure whose detail names no file is worse than
    either outcome, because the number looks real.

    Asking git "are you inside a work tree" instead is not enough either. On a
    machine where the home directory is itself a repository -- which is the case
    on the development machine -- every path under it answers yes, so a folder
    that had nothing to do with Mimir would be inspected against an unrelated
    ancestor repository and pass. So the requirement is the stronger one the
    check actually means: the folder must sit in the *same* repository as this
    script.

    `git ls-files` already restricts itself to the directory it runs in, so
    scoping per folder still works inside one monorepo.
    """

    project = repository_root(Path(__file__).resolve().parent)
    if project is None or repository_root(root) != project:
        return None
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.splitlines() if result.returncode == 0 else None


def forbidden_tracked(files: list[str] | None) -> list[str]:
    if files is None:
        return ["repository could not be inspected"]
    findings = []
    for raw in files:
        path = raw.replace("\\", "/")
        lower = path.lower()
        if any(lower.startswith(prefix.lower()) for prefix in FORBIDDEN_TRACKED_PREFIXES):
            findings.append(path)
        elif lower.endswith(FORBIDDEN_TRACKED_SUFFIXES):
            findings.append(path)
    return findings


def benchmark_inventory(labels_path: Path) -> dict[str, int]:
    counts = {"total": 0, "positive": 0, "hard_negative": 0}
    try:
        with labels_path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                expected = str(row.get("expected_severity") or "").upper()
                category = str(row.get("category") or "").lower()
                counts["total"] += 1
                if expected in {"IMPORTANT", "REVIEW"} and category not in {"person_near"}:
                    counts["positive"] += 1
                if expected == "IGNORE" or category in {"person_near", "person_passby", "normal_traffic"}:
                    counts["hard_negative"] += 1
    except OSError:
        pass
    return counts


def authenticode_status(path: Path) -> str:
    if not path.exists() or sys.platform != "win32":
        return "missing" if not path.exists() else "unsupported_platform"
    command = f"(Get-AuthenticodeSignature -LiteralPath '{str(path).replace("'", "''")}').Status"
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.strip() if result.returncode == 0 else "error"


def locate_installer(frontend_root: Path, explicit: str) -> Path | None:
    if explicit:
        return Path(explicit)
    package_version = frontend_package_version(frontend_root)
    bundle_root = frontend_root / "src-tauri" / "target" / "release" / "bundle"
    candidates = list(bundle_root.glob("nsis/*.exe")) + list(bundle_root.glob("msi/*.msi"))
    if package_version:
        candidates = [candidate for candidate in candidates if package_version in candidate.name]
    return max(candidates, key=lambda item: item.stat().st_mtime) if candidates else None


def evaluate_external_gates(frontend_root: Path, installer: str) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    manifest = read_json(ROOT / "mimir_core_v2" / "model_manifest.json")
    detector_ok = bool(manifest) and not bool(manifest.get("release_blocker")) and bool(
        manifest.get("commercial_distribution_approved")
    ) and str(manifest.get("license") or "") in {"Apache-2.0", "MIT", "BSD-2-Clause", "BSD-3-Clause"}
    add_check(
        checks,
        "permissive_detector",
        detector_ok,
        str(manifest.get("detector_id") or "model manifest missing"),
    )
    models = manifest.get("models") if isinstance(manifest.get("models"), list) else []
    model_provenance_ok = bool(models) and all(
        isinstance(item, dict)
        and item.get("filename")
        and len(str(item.get("sha256") or "")) == 64
        and int(item.get("size_bytes") or 0) > 0
        for item in models
    )
    add_check(checks, "model_provenance", model_provenance_ok, f"{len(models)} model records with checksum and size")
    license_path = ROOT / str(manifest.get("license_file") or "")
    add_check(
        checks,
        "model_license_notice",
        bool(manifest.get("license_file")) and license_path.exists(),
        str(license_path),
    )

    evaluation = read_json(frontend_root / "release_assets" / "evaluation_report.json")
    locked_counts = evaluation.get("locked_test_counts") if isinstance(evaluation.get("locked_test_counts"), dict) else {}
    locked_groups = int(locked_counts.get("groups") or 0)
    locked_positives = int(locked_counts.get("positives") or 0)
    locked_negatives = int(locked_counts.get("hard_negatives") or 0)
    add_check(checks, "locked_evaluation_size", locked_groups >= MIN_LOCKED_LABELS, f"{locked_groups} / {MIN_LOCKED_LABELS} groups")
    add_check(checks, "locked_positive_coverage", locked_positives >= MIN_POSITIVE_LABELS, f"{locked_positives} / {MIN_POSITIVE_LABELS} positives")
    add_check(checks, "locked_hard_negative_coverage", locked_negatives >= MIN_HARD_NEGATIVES, f"{locked_negatives} / {MIN_HARD_NEGATIVES} hard negatives")

    backend_findings = forbidden_tracked(repository_files(ROOT))
    frontend_findings = forbidden_tracked(repository_files(frontend_root))
    all_findings = backend_findings + frontend_findings
    add_check(
        checks,
        "repository_hygiene",
        not all_findings,
        "clean" if not all_findings else f"{len(all_findings)} generated/private/model files still tracked; examples: {', '.join(all_findings[:5])}",
    )

    required_docs = [
        frontend_root / "docs" / "FREE_BETA_TERMS.md",
        frontend_root / "docs" / "PRIVACY.md",
        frontend_root / "docs" / "LIMITATIONS.md",
        frontend_root / "docs" / "MODEL_CARD.md",
    ]
    missing_docs = [path.name for path in required_docs if not path.exists()]
    add_check(checks, "release_documents", not missing_docs, "complete" if not missing_docs else f"missing: {', '.join(missing_docs)}")

    sbom = frontend_root / "release_assets" / "sbom.cdx.json"
    add_check(checks, "sbom", sbom.exists(), str(sbom))

    executables = [
        frontend_root / "src-tauri" / "resources" / "mimir-backend" / "mimir-core-v2-scan.exe",
        frontend_root / "src-tauri" / "resources" / "mimir-backend" / "mimir-core-v2-ai-enrich.exe",
        frontend_root / "src-tauri" / "resources" / "mimir-backend" / "mimir-core-v2-actions.exe",
        frontend_root / "src-tauri" / "resources" / "mimir-backend" / "mimir-core-v2-dataset.exe",
        # Built by build-sidecar.ps1 and signed by sign_release.ps1, but was
        # missing here -- so an unsigned model-update sidecar would have passed
        # this gate. It can replace the active detector, so it must be verified.
        frontend_root / "src-tauri" / "resources" / "mimir-backend" / "mimir-core-v2-model-update.exe",
        frontend_root / "src-tauri" / "resources" / "mimir-backend" / "age.exe",
    ]
    installer_path = locate_installer(frontend_root, installer)
    if installer_path is not None:
        executables.append(installer_path)
    elif not installer:
        package_version = frontend_package_version(frontend_root) or "current package version"
        executables.append(frontend_root / "src-tauri" / "target" / "release" / "bundle" / "nsis" / f"Mimir_{package_version}_x64-setup.exe")
    signature_results = {str(path): authenticode_status(path) for path in executables}
    signed = bool(installer_path) and all(status == "Valid" for status in signature_results.values())
    add_check(checks, "signed_release_artifacts", signed, json.dumps(signature_results, sort_keys=True))

    clean_vm_report = read_json(frontend_root / "release_assets" / "clean_vm_report.json")
    clean_vm_checks = clean_vm_report.get("checks") if isinstance(clean_vm_report.get("checks"), dict) else {}
    required_vm_stages = {"install", "scan", "upgrade", "rollback", "uninstall"}
    clean_vm_ok = bool(clean_vm_report.get("windows_10_passed")) and bool(clean_vm_report.get("windows_11_passed"))
    for platform in ("windows_10", "windows_11"):
        platform_checks = clean_vm_checks.get(platform) if isinstance(clean_vm_checks.get(platform), dict) else {}
        for stage in required_vm_stages:
            stage_record = platform_checks.get(stage) if isinstance(platform_checks.get(stage), dict) else {}
            clean_vm_ok = clean_vm_ok and bool(stage_record.get("passed"))
            clean_vm_ok = clean_vm_ok and stage_record.get("python_on_path") is False
            clean_vm_ok = clean_vm_ok and bool(stage_record.get("os_matches_requested"))
            clean_vm_ok = clean_vm_ok and str(stage_record.get("authenticode_status") or "") == "Valid"
            clean_vm_ok = clean_vm_ok and len(str(stage_record.get("artifact_sha256") or "")) == 64
    add_check(checks, "clean_vm_installation", clean_vm_ok, "Windows 10 and Windows 11 required")

    security_report = read_json(frontend_root / "release_assets" / "security_scan_report.json")
    add_check(checks, "security_scan", bool(security_report.get("passed")), str(security_report.get("summary") or "report missing"))
    accessibility_report = read_json(frontend_root / "release_assets" / "accessibility_report.json")
    add_check(checks, "accessibility", bool(accessibility_report.get("passed")), str(accessibility_report.get("summary") or "report missing"))

    update_report = read_json(frontend_root / "release_assets" / "update_rollback_report.json")
    update_ok = (
        bool(update_report.get("signed_update_passed"))
        and bool(update_report.get("rollback_passed"))
        and bool(update_report.get("corrupt_update_rejected"))
        and bool(update_report.get("failed_update_recovery_passed"))
        and bool(update_report.get("sessions_preserved"))
    )
    add_check(checks, "signed_update_and_rollback", update_ok, str(update_report.get("summary") or "report missing"))

    candidate_metrics = evaluation.get("candidate") if isinstance(evaluation.get("candidate"), dict) else {}
    timing_metrics = candidate_metrics.get("timing_error_distribution_sec") if isinstance(candidate_metrics.get("timing_error_distribution_sec"), dict) else {}
    reliability = read_json(frontend_root / "release_assets" / "reliability_report.json")
    reliability_report_valid = reliability.get("schema_version") == "mimir_reliability_report_v1"
    reliability_runs = reliability.get("runs") if isinstance(reliability.get("runs"), list) else []
    packaged_scanner = frontend_root / "src-tauri" / "resources" / "mimir-backend" / "mimir-core-v2-scan.exe"
    reported_scanner = Path(str(reliability.get("scanner") or ""))
    reliability_scanner_matches_candidate = (
        reported_scanner.suffix.lower() == ".exe"
        and len(str(reliability.get("scanner_sha256") or "")) == 64
        and str(reliability.get("scanner_sha256") or "").lower() == sha256_file(packaged_scanner).lower()
    )
    evaluation_requirements = {
        "candidate_promotion_allowed": bool(evaluation.get("promotion_allowed")),
        "critical_impact_recall": float(candidate_metrics.get("critical_recall") or 0) >= 0.99,
        "critical_false_ignores": int(candidate_metrics.get("false_ignores") or 0) == 0,
        "door_contact_recall": float(candidate_metrics.get("door_contact_recall") or 0) >= 0.97,
        "false_important_rate": float(candidate_metrics.get("false_important_rate") if candidate_metrics.get("false_important_rate") is not None else 1) <= 0.005,
        "key_moment_median_error_sec": float(timing_metrics.get("median") if timing_metrics.get("median") is not None else 999) <= 0.5,
        "key_moment_p95_error_sec": float(timing_metrics.get("p95") if timing_metrics.get("p95") is not None else 999) <= 1.0,
        "automated_e2e_scans": int(reliability.get("completed_runs") or 0) >= 500,
        "reliability_report_schema": reliability_report_valid,
        "reliability_run_records": len(reliability_runs) >= 500,
        "reliability_zero_failed_runs": reliability_report_valid and int(reliability.get("failed_runs") or 0) == 0,
        "reliability_required_fixtures": bool(reliability.get("required_fixtures_complete")),
        "reliability_required_scenarios": bool(reliability.get("required_scenarios_complete")),
        "reliability_packaged_scanner_parity": reliability_scanner_matches_candidate,
        "reliability_zero_classification_failures": reliability_report_valid and all(
            int(reliability.get(name) or 0) == 0
            for name in ("critical_failures", "false_importants", "false_ignores")
        ),
        "media_availability": float(reliability.get("thumbnail_video_availability") or 0) >= 0.995,
        "source_byte_identical": bool(reliability.get("source_byte_identical")),
        "reliability_gate": bool(reliability.get("release_gate_passed")),
    }
    failed_metrics = [name for name, passed in evaluation_requirements.items() if not passed]
    add_check(checks, "release_metrics", not failed_metrics, "complete" if not failed_metrics else f"missing/failing: {', '.join(failed_metrics)}")
    return checks


def run_operational_checks(args: argparse.Namespace, output_dir: Path) -> dict[str, Any]:
    for target in COMPILE_TARGETS:
        path = ROOT / target
        if not path.exists():
            raise ReleaseCheckFailed(f"compile target missing: {target}")
        run_required([sys.executable, "-m", "py_compile", str(path)], f"compile {target}")
    for module in (
        "mimir_core_v2.test_release_foundation",
        "mimir_core_v2.test_storage_actions",
        "mimir_core_v2.test_resolver",
        "mimir_core_v2.test_ai_guardrails",
        "mimir_core_v2.test_data_pipeline",
        "mimir_core_v2.test_otw_auxiliary",
        "mimir_core_v2.test_carla_data",
        # Regression guards for the sampling-rate bug class: several thresholds
        # were once expressed as raw sampled-frame counts, so the slower scan
        # modes behaved worse than the fast one. The gate must actually run
        # these, not merely compile them.
        "mimir_core_v2.test_mode_invariance",
        "mimir_core_v2.test_dwell_flags",
        "mimir_core_v2.test_motion_baseline",
        "mimir_core_v2.test_selection_grid",
        # Guards that a malformed or tampered model package cannot become the
        # active detector.
        "mimir_core_v2.test_model_update",
    ):
        run_required([sys.executable, "-m", module], module)

    if not args.input:
        raise ReleaseCheckFailed("--input is required unless --gate-only is used")
    run_required(
        [sys.executable, "-m", "mimir_core_v2.test_onnx_detector", "--input", args.input],
        "RF-DETR real-footage smoke test",
    )
    session_path = output_dir / "latest_session.json"
    benchmark_report = output_dir / "benchmark_report.json"
    source_before = source_video_hashes(args.input)
    if not source_before:
        raise ReleaseCheckFailed("input folder contains no MP4 source footage")
    started = time.perf_counter()
    run_required(
        [sys.executable, str(ROOT / "mimir_core_v2_scan.py"), "--input", args.input, "--mode", "balanced", "--output", str(output_dir)],
        "Core v2 scan",
    )
    source_after = source_video_hashes(args.input)
    if source_after != source_before:
        raise ReleaseCheckFailed("source footage changed during scan")
    benchmark_command = [
        sys.executable,
        "-m",
        "mimir_core_v2.benchmark",
        "--session",
        str(session_path),
        "--report",
        str(benchmark_report),
        "--strict",
    ]
    if args.source_set:
        benchmark_command.extend(["--source-set", args.source_set])
    run_required(benchmark_command, "benchmark")
    benchmark = read_json(benchmark_report)
    if any(int(benchmark.get(name) or 0) for name in ("failed", "critical_failures", "false_importants", "false_ignores")):
        raise ReleaseCheckFailed("benchmark contains a failure")
    session = read_json(session_path)
    if not session:
        raise ReleaseCheckFailed(f"session missing: {session_path}")
    return {
        "passed": True,
        "runtime_sec": round(time.perf_counter() - started, 3),
        "session_id": session.get("session_id"),
        "incidents": len(session.get("incidents") or []),
        "source_videos_hashed": len(source_before),
        "source_byte_identical": True,
        "benchmark": benchmark,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Mimir Core v2 beta release gates.")
    parser.add_argument("--input", help="Real footage folder for operational validation.")
    parser.add_argument("--source-set", default="", help="Benchmark source set for the input folder.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_DIR / "release_check"))
    parser.add_argument("--frontend-root", default=str(DEFAULT_FRONTEND_ROOT))
    parser.add_argument("--installer", default="")
    parser.add_argument("--gate-only", action="store_true", help="Evaluate external gates without scanning footage.")
    parser.add_argument("--internal", action="store_true", help="Dogfood mode: report external blockers without failing on them.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output_dir = Path(args.output).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    report_name = "release_gate_report.json" if args.gate_only else "release_check_report.json"
    report_path = output_dir / report_name
    operational: dict[str, Any] = {"passed": True, "skipped": bool(args.gate_only)}
    operational_error = ""
    try:
        if not args.gate_only:
            operational = run_operational_checks(args, output_dir)
    except ReleaseCheckFailed as exc:
        operational = {"passed": False, "skipped": False}
        operational_error = str(exc)

    gates = evaluate_external_gates(Path(args.frontend_root).resolve(), args.installer)
    external_ready = all(item["passed"] for item in gates if item["blocking"])
    passed = bool(operational.get("passed")) and (external_ready or args.internal)
    report = {
        "schema_version": "mimir_release_check_v2",
        "generated_at": utc_now(),
        "release_channel": "free_public_beta",
        "billing_enabled": False,
        "activation_required": False,
        "internal_mode": bool(args.internal),
        "operational": operational,
        "operational_error": operational_error,
        "external_release_ready": external_ready,
        "external_gates": gates,
        "passed": passed,
    }
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    print("\nMimir free beta release readiness")
    print("=================================")
    operational_label = "SKIPPED" if operational.get("skipped") else ("PASS" if operational.get("passed") else "FAIL")
    print(f"operational checks: {operational_label}")
    for item in gates:
        print(f"[{'PASS' if item['passed'] else 'BLOCK'}] {item['name']}: {item['detail']}")
    print(f"external release ready: {'YES' if external_ready else 'NO'}")
    print(f"report: {report_path}")
    if passed:
        print("MIMIR INTERNAL CHECK PASSED" if args.internal and not external_ready else "MIMIR FREE BETA RELEASE CHECK PASSED")
        return 0
    print("MIMIR FREE BETA RELEASE CHECK FAILED")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
