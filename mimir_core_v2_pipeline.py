"""Automate the consented-contribution pipeline up to the human promotion gate.

Contributions arrive as encrypted `.mimir-dataset.age` packages. Until now every
step after that was manual: notice the package, run intake, remember to check
whether enough data had accumulated to attempt a candidate model. That manual
gap is why contributing feedback felt like it went nowhere.

This script closes that gap. It watches an inbox folder, runs intake on anything
new (decrypt -> validate -> deduplicate -> split -> optional CVAT tasks), records
what happened, and reports concrete progress toward the pilot gate so every
contribution visibly moves a number.

What it deliberately does NOT do: promote a model. Training a candidate and
deciding it is good enough to ship stays a human decision, because a candidate
that regresses real safety detection must never reach users automatically.

    python mimir_core_v2_pipeline.py status --dataset-root <dir>
    python mimir_core_v2_pipeline.py process --inbox <dir> --identity <key> --dataset-root <dir>
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mimir_core_v2.dataset_package import DatasetPackageError, intake_package
from mimir_core_v2_training import audit_dataset


PACKAGE_SUFFIX = ".mimir-dataset.age"
PIPELINE_LOG = "pipeline_log.json"

# Mirrors the pilot gate defaults in mimir_core_v2_training.py. Kept here so
# progress can be reported without importing argparse defaults.
PILOT_GATE = {
    "groups": 100,
    "positives": 25,
    "hard_negatives": 25,
    "frames": 250,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def pipeline_log_path(dataset_root: Path) -> Path:
    return dataset_root / PIPELINE_LOG


def load_log(dataset_root: Path) -> dict[str, Any]:
    log = read_json(pipeline_log_path(dataset_root))
    if not log:
        log = {"schema_version": "mimir_pipeline_log_v1", "processed": []}
    if not isinstance(log.get("processed"), list):
        log["processed"] = []
    return log


def processed_names(log: dict[str, Any]) -> set[str]:
    return {
        str(entry.get("package"))
        for entry in log["processed"]
        if isinstance(entry, dict) and entry.get("package")
    }


def gate_progress(dataset_root: Path) -> dict[str, Any]:
    """Report concrete progress toward the pilot gate.

    Returns counts alongside their targets so a caller can render
    "14 / 100" style progress rather than a bare pass/fail.
    """
    audit = audit_dataset(dataset_root)
    current = {
        "groups": int(audit.get("complete_items") or 0),
        "positives": int(audit.get("positive_items") or 0),
        "hard_negatives": int(audit.get("hard_negative_items") or 0),
    }
    remaining = {key: max(0, PILOT_GATE[key] - value) for key, value in current.items()}
    return {
        "generated_at": utc_now(),
        "collections": int(audit.get("collections") or 0),
        "items": int(audit.get("items") or 0),
        "current": current,
        "targets": {key: PILOT_GATE[key] for key in current},
        "remaining": remaining,
        "pilot_gate_met": all(value == 0 for value in remaining.values()),
        "blind_relabels": int(audit.get("blind_relabel_items") or 0),
        "blind_relabels_required": int(audit.get("blind_relabel_required") or 0),
        "audit_errors": list(audit.get("errors") or []),
    }


def print_progress(progress: dict[str, Any]) -> None:
    current = progress["current"]
    targets = progress["targets"]
    print("Mimir contribution progress")
    print("===========================")
    print(f"collections: {progress['collections']}   items: {progress['items']}")
    for key, label in (
        ("groups", "complete groups"),
        ("positives", "positives"),
        ("hard_negatives", "hard negatives"),
    ):
        done = current[key]
        target = targets[key]
        bar_width = 24
        filled = min(bar_width, int(bar_width * done / target)) if target else 0
        bar = "#" * filled + "-" * (bar_width - filled)
        print(f"{label:>16}: [{bar}] {done} / {target}")
    print(
        f"{'blind re-labels':>16}: {progress['blind_relabels']} / "
        f"{progress['blind_relabels_required']} required"
    )
    if progress["pilot_gate_met"]:
        print()
        print("PILOT GATE MET - a candidate training run can now be attempted.")
        print("Run: python mimir_core_v2_training.py prepare ... then train")
        print("A candidate still requires human evaluation and explicit promotion.")
    else:
        needed = ", ".join(
            f"{value} more {key.replace('_', ' ')}"
            for key, value in progress["remaining"].items()
            if value > 0
        )
        print()
        print(f"Still needed for the pilot gate: {needed}")
    for error in progress["audit_errors"][:10]:
        print(f"ERROR: {error}")


def status_command(args: argparse.Namespace) -> int:
    dataset_root = Path(args.dataset_root)
    progress = gate_progress(dataset_root)
    if args.json:
        print(json.dumps(progress, indent=2))
    else:
        print_progress(progress)
        log = load_log(dataset_root)
        if log["processed"]:
            print()
            print(f"packages processed: {len(log['processed'])}")
            for entry in log["processed"][-5:]:
                if isinstance(entry, dict):
                    print(f"  {entry.get('processed_at', '?')}  {entry.get('package', '?')}  {entry.get('status', '?')}")
    if args.report:
        write_json(Path(args.report), progress)
        print(f"report: {args.report}")
    return 0


def process_command(args: argparse.Namespace) -> int:
    inbox = Path(args.inbox)
    dataset_root = Path(args.dataset_root)
    identity = Path(args.identity)

    if not inbox.is_dir():
        print(f"Inbox folder does not exist: {inbox}", file=sys.stderr)
        return 1
    if not identity.is_file():
        print(f"Intake identity key not found: {identity}", file=sys.stderr)
        return 1

    log = load_log(dataset_root)
    seen = processed_names(log)
    packages = sorted(
        path for path in inbox.iterdir()
        if path.is_file() and path.name.endswith(PACKAGE_SUFFIX) and path.name not in seen
    )

    if not packages:
        print("No new contribution packages found.")
        print_progress(gate_progress(dataset_root))
        return 0

    print(f"Found {len(packages)} new contribution package(s).")
    processed_dir = inbox / "processed"
    failed_dir = inbox / "failed"
    succeeded = 0

    for package in packages:
        print(f"\n-> {package.name}")
        entry: dict[str, Any] = {"package": package.name, "processed_at": utc_now()}
        try:
            result = intake_package(
                package,
                identity,
                dataset_root,
                cvat_url=args.cvat_url if args.create_cvat_tasks else "",
                cvat_token=args.cvat_token,
                cvat_token_file=args.cvat_token_file,
            )
            entry["status"] = "intake_ok"
            entry["result"] = result
            succeeded += 1
            print("   intake OK")
            if args.move_processed:
                processed_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(package), str(processed_dir / package.name))
                print(f"   moved to {processed_dir}")
        except (DatasetPackageError, OSError, ValueError) as exc:
            entry["status"] = "failed"
            entry["error"] = f"{type(exc).__name__}: {exc}"
            print(f"   FAILED: {exc}", file=sys.stderr)
            if args.move_processed:
                failed_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(package), str(failed_dir / package.name))
                print(f"   moved to {failed_dir}")

        log["processed"].append(entry)

    log["updated_at"] = utc_now()
    write_json(pipeline_log_path(dataset_root), log)

    print(f"\n{succeeded} of {len(packages)} package(s) taken in successfully.")
    print()
    progress = gate_progress(dataset_root)
    print_progress(progress)
    if args.report:
        write_json(Path(args.report), progress)
        print(f"report: {args.report}")
    return 0 if succeeded == len(packages) else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    status = commands.add_parser("status", help="Report progress toward the pilot gate.")
    status.add_argument("--dataset-root", required=True)
    status.add_argument("--report", default="")
    status.add_argument("--json", action="store_true")

    process = commands.add_parser("process", help="Take in every new contribution package in the inbox.")
    process.add_argument("--inbox", required=True)
    process.add_argument("--dataset-root", required=True)
    process.add_argument("--identity", required=True, help="age identity key file for decryption")
    process.add_argument("--create-cvat-tasks", action="store_true")
    process.add_argument("--cvat-url", default="http://localhost:8080")
    process.add_argument("--cvat-token", default="")
    process.add_argument("--cvat-token-file", default="")
    process.add_argument("--move-processed", action="store_true", help="Move handled packages into processed/ or failed/")
    process.add_argument("--report", default="")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "status":
            return status_command(args)
        if args.command == "process":
            return process_command(args)
    except (DatasetPackageError, OSError, ValueError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
