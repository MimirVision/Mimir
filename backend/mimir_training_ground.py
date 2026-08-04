r"""Mimir Training Ground: pull in what testers have submitted, and track progress.

Replaces the developer side of what used to be "wait for an email, save the
attachment somewhere, remember to run intake, remember to check the gate."
This script does all three, in one place, for both contributions and
feedback:

    python mimir_training_ground.py sync --dataset-root <dir> --inbox <dir> \
        --feedback-inbox <dir> --identity <key>
    python mimir_training_ground.py status [--json] --dataset-root <dir>
    python mimir_training_ground.py feedback list [--json] --feedback-inbox <dir>
    python mimir_training_ground.py feedback show <package_id> [--json] --feedback-inbox <dir>
    python mimir_training_ground.py collections list [--json] --dataset-root <dir>
    python mimir_training_ground.py collections show <package_id> --dataset-root <dir> \
        [--cvat-url <url> --cvat-token <token>]

`status`/`feedback list`/`feedback show`/`collections list` accept `--json`
for a machine-readable version of the same data (built for Mimir Forge, the
GUI companion app -- see C:\MimirDev\forge). `sync` always prints one final
`MIMIR_SYNC_RESULT_JSON: {...}` line alongside its normal progress output,
for the same reason. `collections show` is always JSON -- it's a detail
dump, there's no useful prose form of it.

`sync` has two phases, kept deliberately separate so a failure in one does
not corrupt the other:

1. Download anything new from R2 into local inbox folders. This step never
   decrypts anything -- it is the exact equivalent of a tester having handed
   you a USB drive with encrypted files on it, just automated. A local
   `sync_log.json` (download bookkeeping) tracks what has already been
   pulled, separate from `pipeline_log.json` (intake bookkeeping, owned by
   mimir_core_v2_pipeline.py) -- two different failure modes, so two
   different logs: re-downloading something already intaken is wasted
   bandwidth, but re-intaking something already downloaded is a correctness
   bug. Neither log substitutes for the other.
2. Intake whatever is now in the inbox folders: contributions go through
   the existing mimir_core_v2_pipeline.py `process` step unchanged (decrypt,
   validate, deduplicate, split, optional CVAT tasks -- all reused, none of
   it duplicated here). Feedback packages go through the new, much lighter
   intake_feedback_package from feedback_package.py.

Requires an R2 (or any S3-compatible) API token with read+list access to the
intake bucket -- a credential that lives only on the machine running this
script, in `MIMIR_R2_*` environment variables, never in either git repo.
This is a different, developer-only credential from the write-only app
token the Cloudflare Worker checks; this script never talks to the Worker
at all, only directly to R2's S3-compatible API.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import mimir_core_v2_pipeline as pipeline
from mimir_core_v2.dataset_package import DatasetPackageError
from mimir_core_v2.feedback_package import intake_feedback_package

INTAKE_REGISTRY = "intake_registry.json"

SYNC_LOG = "sync_log.json"
CONTRIBUTION_PREFIX = "contributions/"
FEEDBACK_PREFIX = "feedback/"


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


def load_sync_log(dataset_root: Path) -> dict[str, Any]:
    log = read_json(dataset_root / SYNC_LOG)
    if not log:
        log = {"schema_version": "mimir_sync_log_v1", "downloaded": []}
    if not isinstance(log.get("downloaded"), list):
        log["downloaded"] = []
    return log


def downloaded_keys(log: dict[str, Any]) -> set[str]:
    return {str(entry.get("object_key")) for entry in log["downloaded"] if isinstance(entry, dict)}


class R2Config:
    def __init__(self, args: argparse.Namespace) -> None:
        self.endpoint = args.r2_endpoint or os.environ.get("MIMIR_R2_ENDPOINT", "")
        self.bucket = args.r2_bucket or os.environ.get("MIMIR_R2_BUCKET", "mimir-intake")
        self.access_key_id = os.environ.get("MIMIR_R2_ACCESS_KEY_ID", "")
        self.secret_access_key = os.environ.get("MIMIR_R2_SECRET_ACCESS_KEY", "")

    def validate(self) -> None:
        missing = [
            name
            for name, value in (
                ("MIMIR_R2_ENDPOINT (or --r2-endpoint)", self.endpoint),
                ("MIMIR_R2_ACCESS_KEY_ID", self.access_key_id),
                ("MIMIR_R2_SECRET_ACCESS_KEY", self.secret_access_key),
            )
            if not value
        ]
        if missing:
            raise SystemExit(
                "Missing R2 credentials: " + ", ".join(missing) + ". "
                "See C:\\MimirDev\\ingest-worker\\README.md for where these come from."
            )


def r2_client(config: R2Config):
    try:
        import boto3
    except ImportError as exc:
        raise SystemExit(
            "boto3 is required for `sync` -- install with: "
            "pip install -r requirements-training-ground.txt"
        ) from exc

    return boto3.client(
        "s3",
        endpoint_url=config.endpoint,
        aws_access_key_id=config.access_key_id,
        aws_secret_access_key=config.secret_access_key,
        region_name="auto",
    )


def list_new_objects(client, bucket: str, prefix: str, already_downloaded: set[str]) -> list[str]:
    keys: list[str] = []
    continuation: str | None = None
    while True:
        kwargs: dict[str, Any] = {"Bucket": bucket, "Prefix": prefix}
        if continuation:
            kwargs["ContinuationToken"] = continuation
        response = client.list_objects_v2(**kwargs)
        for item in response.get("Contents", []):
            key = str(item.get("Key") or "")
            if key and key not in already_downloaded and not key.endswith("/"):
                keys.append(key)
        if not response.get("IsTruncated"):
            break
        continuation = response.get("NextContinuationToken")
    return sorted(keys)


def download_phase(args: argparse.Namespace) -> tuple[list[Path], list[Path]]:
    """Phase 1: pull new objects from R2 into the local inbox folders.
    Returns (new_contribution_files, new_feedback_files) actually written.
    Never decrypts anything.
    """
    dataset_root = Path(args.dataset_root)
    inbox = Path(args.inbox)
    feedback_inbox_downloads = Path(args.feedback_inbox_downloads)
    inbox.mkdir(parents=True, exist_ok=True)
    feedback_inbox_downloads.mkdir(parents=True, exist_ok=True)

    config = R2Config(args)
    config.validate()
    client = r2_client(config)

    log = load_sync_log(dataset_root)
    seen = downloaded_keys(log)

    contribution_keys = list_new_objects(client, config.bucket, CONTRIBUTION_PREFIX, seen)
    feedback_keys = list_new_objects(client, config.bucket, FEEDBACK_PREFIX, seen)

    new_contributions: list[Path] = []
    new_feedback: list[Path] = []

    for key in contribution_keys:
        destination = inbox / Path(key).name
        client.download_file(config.bucket, key, str(destination))
        new_contributions.append(destination)
        log["downloaded"].append({"object_key": key, "downloaded_at": utc_now(), "kind": "contribution"})
        print(f"downloaded contribution: {key}")

    for key in feedback_keys:
        destination = feedback_inbox_downloads / Path(key).name
        client.download_file(config.bucket, key, str(destination))
        new_feedback.append(destination)
        log["downloaded"].append({"object_key": key, "downloaded_at": utc_now(), "kind": "feedback"})
        print(f"downloaded feedback: {key}")

    log["updated_at"] = utc_now()
    write_json(dataset_root / SYNC_LOG, log)

    if not new_contributions and not new_feedback:
        print("No new submissions.")

    return new_contributions, new_feedback


def intake_feedback_phase(feedback_downloads: Path, feedback_inbox: Path, identity: Path) -> list[dict[str, Any]]:
    """Phase 2, feedback half: decrypt and file each downloaded feedback
    package. Mirrors process_command's per-item try/except shape, but
    against the much smaller feedback intake path -- no CVAT, no splits.
    Returns per-item results so callers (sync_command's JSON summary) don't
    have to re-derive what happened from stdout.
    """
    packages = sorted(
        path for path in feedback_downloads.iterdir() if path.is_file() and path.name.endswith(".mimir-feedback.age")
    )
    if not packages:
        return []

    print(f"\nIntaking {len(packages)} feedback package(s)...")
    results: list[dict[str, Any]] = []
    for package in packages:
        try:
            result = intake_feedback_package(package, identity, feedback_inbox)
            print(f"  {package.name}: {result['status']} ({result['package_id']})")
            results.append({"file": package.name, "status": result["status"], "package_id": result["package_id"]})
        except DatasetPackageError as exc:
            print(f"  {package.name}: FAILED -- {exc}", file=sys.stderr)
            results.append({"file": package.name, "status": "failed", "error": str(exc)})
    return results


def sync_command(args: argparse.Namespace) -> int:
    new_contributions, new_feedback = download_phase(args)

    contribution_exit_code: int | None = None
    contribution_results: list[dict[str, Any]] = []
    if new_contributions:
        pipeline_args = argparse.Namespace(
            inbox=args.inbox,
            dataset_root=args.dataset_root,
            identity=args.identity,
            create_cvat_tasks=args.create_cvat_tasks,
            cvat_url=args.cvat_url,
            cvat_token=args.cvat_token,
            cvat_token_file=args.cvat_token_file,
            move_processed=True,
            report="",
        )
        print(f"\nIntaking {len(new_contributions)} contribution package(s)...")
        contribution_exit_code = pipeline.process_command(pipeline_args)
        # process_command already wrote per-package outcomes to
        # pipeline_log.json (mimir_core_v2_pipeline.py's own `log["processed"]`
        # list) -- read the tail back rather than duplicating that bookkeeping,
        # so Forge can show *why* a contribution failed instead of just a
        # pass/fail count. Safe to take the last N entries: process_command
        # appends exactly one entry per package it just processed, in order.
        log = pipeline.load_log(Path(args.dataset_root))
        for entry in log["processed"][-len(new_contributions):]:
            if not isinstance(entry, dict):
                continue
            item = {"file": entry.get("package", ""), "status": entry.get("status", "")}
            if entry.get("status") == "failed":
                item["error"] = entry.get("error", "")
            contribution_results.append(item)
    elif not new_feedback:
        # Nothing new at all -- still show progress, matching
        # process_command's own behavior when its inbox is empty.
        pipeline.print_progress(pipeline.gate_progress(Path(args.dataset_root)))

    feedback_results: list[dict[str, Any]] = []
    if new_feedback:
        feedback_results = intake_feedback_phase(
            Path(args.feedback_inbox_downloads), Path(args.feedback_inbox), Path(args.identity)
        )

    # One final structured line, on top of the human-readable progress
    # already printed above -- mirrors the MIMIR_PACKAGE_JSON: marker added
    # to mimir_core_v2_dataset.py's export-encrypted/export-feedback this
    # session, for a caller (Mimir Forge's Rust layer) that needs the
    # outcome without scraping prose.
    result = {
        "schema_version": "mimir_sync_result_v1",
        "synced_at": utc_now(),
        "new_contribution_count": len(new_contributions),
        "new_feedback_count": len(new_feedback),
        "contribution_intake_exit_code": contribution_exit_code,
        "contribution_results": contribution_results,
        "feedback_results": feedback_results,
        "gate_progress": pipeline.gate_progress(Path(args.dataset_root)),
    }
    print("MIMIR_SYNC_RESULT_JSON: " + json.dumps(result))

    return 0


def status_command(args: argparse.Namespace) -> int:
    progress = pipeline.gate_progress(Path(args.dataset_root))
    if args.json:
        print(json.dumps(progress, indent=2))
    else:
        pipeline.print_progress(progress)
    return 0


def feedback_list_command(args: argparse.Namespace) -> int:
    feedback_inbox = Path(args.feedback_inbox)
    entries: list[dict[str, Any]] = []
    if feedback_inbox.is_dir():
        for folder in sorted(feedback_inbox.iterdir()):
            feedback_file = folder / "feedback.json"
            if not folder.is_dir() or not feedback_file.is_file():
                continue
            entries.append({"package_id": folder.name, "feedback": read_json(feedback_file)})

    if args.json:
        print(json.dumps({"items": entries}, indent=2))
        return 0

    if not entries:
        print("No feedback received yet.")
        return 0

    print(f"{len(entries)} feedback item(s):\n")
    for entry in entries:
        feedback = entry["feedback"]
        choice = feedback.get("user_selected_feedback", "?")
        incident = feedback.get("incident_id", "?")
        timestamp = feedback.get("timestamp", feedback.get("saved_at", "?"))
        print(f"  {entry['package_id'][:12]}...  {timestamp}  [{choice}]  incident {incident}")
    print("\nUse `feedback show <package_id>` for the full record.")
    return 0


def feedback_show_command(args: argparse.Namespace) -> int:
    feedback_inbox = Path(args.feedback_inbox)
    matches = [folder for folder in feedback_inbox.iterdir() if folder.is_dir() and folder.name.startswith(args.package_id)]
    if not matches:
        print(f"No feedback found matching id {args.package_id!r}.", file=sys.stderr)
        return 1
    if len(matches) > 1:
        print(f"Ambiguous id prefix {args.package_id!r} matches {len(matches)} entries.", file=sys.stderr)
        return 1

    folder = matches[0]
    feedback = read_json(folder / "feedback.json")
    video_path = ""
    video_dir = folder / "video"
    if video_dir.is_dir():
        videos = sorted(item.name for item in video_dir.iterdir())
        if videos:
            video_path = str(video_dir / videos[0])

    if args.json:
        print(json.dumps({"package_id": folder.name, "feedback": feedback, "video_path": video_path}, indent=2))
        return 0

    print(json.dumps(feedback, indent=2))
    if video_path:
        print(f"\nAttached video: {video_path}")
    return 0


def _collection_summary(package_id: str, record: dict[str, Any]) -> dict[str, Any]:
    return {
        "package_id": package_id,
        "imported_at": record.get("imported_at", ""),
        "split": record.get("split", ""),
        "cvat_status": record.get("cvat_status", "not_requested"),
        "cvat_task_count": len(record.get("cvat_tasks") or []),
        "duplicate_media_rejected": record.get("duplicate_media_rejected", 0),
    }


def collections_list_command(args: argparse.Namespace) -> int:
    registry = read_json(Path(args.dataset_root) / INTAKE_REGISTRY)
    packages = registry.get("packages") if isinstance(registry.get("packages"), dict) else {}
    items = [
        _collection_summary(package_id, record)
        for package_id, record in sorted(packages.items())
        if isinstance(record, dict)
    ]

    if args.json:
        print(json.dumps({"items": items}, indent=2))
        return 0

    if not items:
        print("No contributions received yet.")
        return 0

    print(f"{len(items)} collection(s):\n")
    for item in items:
        print(
            f"  {item['package_id'][:12]}...  {item['imported_at']}  split={item['split']}  "
            f"cvat={item['cvat_status']} ({item['cvat_task_count']} task(s))"
        )
    print("\nUse `collections show <package_id>` for detail.")
    return 0


def collections_show_command(args: argparse.Namespace) -> int:
    dataset_root = Path(args.dataset_root)
    registry = read_json(dataset_root / INTAKE_REGISTRY)
    packages = registry.get("packages") if isinstance(registry.get("packages"), dict) else {}
    matches = [package_id for package_id in packages if package_id.startswith(args.package_id)]
    if not matches:
        print(f"No collection found matching id {args.package_id!r}.", file=sys.stderr)
        return 1
    if len(matches) > 1:
        print(f"Ambiguous id prefix {args.package_id!r} matches {len(matches)} entries.", file=sys.stderr)
        return 1

    package_id = matches[0]
    record = packages[package_id]
    collection_dir = dataset_root / "collections" / package_id
    consent = read_json(collection_dir / "consent.json")
    manifest = read_json(collection_dir / "manifest.json")
    items = manifest.get("items")
    item_count = len(items) if isinstance(items, list) else 0

    # Live CVAT status is the one genuinely new network call here -- every
    # other field in `result` is already sitting on disk. Only attempted if
    # a CVAT URL was given; a CVAT outage degrades this one field, not the
    # whole command, since CvatClient.request() only ever raises CvatError
    # (it wraps every urllib failure), never a bare network exception.
    live_tasks: list[dict[str, Any]] = []
    if args.cvat_url:
        from mimir_core_v2.cvat_client import CvatClient, CvatError, configured_token

        try:
            client = CvatClient(args.cvat_url, configured_token(args.cvat_token, args.cvat_token_file))
        except CvatError as exc:
            live_tasks = [{"error": str(exc)}]
        else:
            for task in record.get("cvat_tasks") or []:
                task_id = task.get("task_id")
                if task_id is None:
                    continue
                try:
                    live = client.task(int(task_id))
                    live_tasks.append(
                        {
                            "task_id": task_id,
                            "name": task.get("name", ""),
                            "status": live.get("status") or live.get("state") or "",
                            "size": live.get("size"),
                        }
                    )
                except CvatError as exc:
                    live_tasks.append({"task_id": task_id, "name": task.get("name", ""), "error": str(exc)})

    result = {
        "package_id": package_id,
        "record": record,
        "consent": {
            "recorded_by": consent.get("recorded_by", ""),
            "rights_basis": consent.get("rights_basis", ""),
            "permission_reference": consent.get("permission_reference", ""),
        },
        "item_count": item_count,
        "live_cvat_tasks": live_tasks,
    }
    print(json.dumps(result, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)

    sync = commands.add_parser("sync", help="Pull new submissions from R2 and intake them.")
    sync.add_argument("--dataset-root", required=True)
    sync.add_argument("--inbox", required=True, help="Local folder for downloaded .mimir-dataset.age files")
    sync.add_argument("--feedback-inbox", required=True, help="Where decrypted feedback is filed")
    sync.add_argument(
        "--feedback-inbox-downloads",
        default="",
        help="Local folder for downloaded (still-encrypted) .mimir-feedback.age files "
        "(default: <feedback-inbox>/_downloads)",
    )
    sync.add_argument("--identity", required=True, help="age identity key file for decryption")
    sync.add_argument("--r2-endpoint", default="")
    sync.add_argument("--r2-bucket", default="")
    sync.add_argument("--create-cvat-tasks", action="store_true")
    sync.add_argument("--cvat-url", default="http://localhost:8080")
    sync.add_argument("--cvat-token", default="")
    sync.add_argument("--cvat-token-file", default="")

    status = commands.add_parser("status", help="Report progress toward the pilot gate.")
    status.add_argument("--dataset-root", required=True)
    status.add_argument("--json", action="store_true")

    feedback = commands.add_parser("feedback", help="Browse received feedback.")
    feedback_commands = feedback.add_subparsers(dest="feedback_command", required=True)
    feedback_list = feedback_commands.add_parser("list", help="List received feedback.")
    feedback_list.add_argument("--feedback-inbox", required=True)
    feedback_list.add_argument("--json", action="store_true")
    feedback_show = feedback_commands.add_parser("show", help="Show one feedback item in full.")
    feedback_show.add_argument("package_id")
    feedback_show.add_argument("--feedback-inbox", required=True)
    feedback_show.add_argument("--json", action="store_true")

    collections = commands.add_parser("collections", help="Browse received contributions.")
    collections_commands = collections.add_subparsers(dest="collections_command", required=True)
    collections_list = collections_commands.add_parser("list", help="List received contribution collections.")
    collections_list.add_argument("--dataset-root", required=True)
    collections_list.add_argument("--json", action="store_true")
    collections_show = collections_commands.add_parser("show", help="Show one collection in full, always as JSON.")
    collections_show.add_argument("package_id")
    collections_show.add_argument("--dataset-root", required=True)
    collections_show.add_argument("--cvat-url", default="", help="If given, also fetch live CVAT task status")
    collections_show.add_argument("--cvat-token", default="")
    collections_show.add_argument("--cvat-token-file", default="")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "sync" and not args.feedback_inbox_downloads:
        args.feedback_inbox_downloads = str(Path(args.feedback_inbox) / "_downloads")

    try:
        if args.command == "sync":
            return sync_command(args)
        if args.command == "status":
            return status_command(args)
        if args.command == "feedback":
            if args.feedback_command == "list":
                return feedback_list_command(args)
            return feedback_show_command(args)
        if args.command == "collections":
            if args.collections_command == "list":
                return collections_list_command(args)
            return collections_show_command(args)
    except (DatasetPackageError, OSError, ValueError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
