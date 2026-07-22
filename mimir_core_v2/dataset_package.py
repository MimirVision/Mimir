"""Encrypted contribution package and idempotent intake primitives."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .cvat_client import CvatClient, CvatError, configured_token


PACKAGE_SCHEMA = "mimir_contribution_package_v1"
EXCLUSIONS_PATH = Path(__file__).with_name("training_exclusions.json")
MAX_PACKAGE_UNCOMPRESSED_BYTES = 500 * 1024 * 1024 * 1024
MAX_PACKAGE_FILES = 10000


class DatasetPackageError(RuntimeError):
    """Raised when contribution packaging or intake cannot proceed safely."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DatasetPackageError(f"Invalid JSON file {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise DatasetPackageError(f"Expected a JSON object: {path}")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def exclusion_hashes() -> dict[str, str]:
    exclusions = read_json(EXCLUSIONS_PATH)
    return {
        str(item.get("sha256") or "").lower(): str(item.get("reason") or "excluded")
        for item in exclusions.get("files", [])
        if isinstance(item, dict) and item.get("sha256")
    }


def find_age_executable(name: str = "age.exe") -> Path:
    configured = os.environ.get("MIMIR_AGE_EXE", "").strip()
    if configured:
        candidate = Path(configured)
        if candidate.name.lower() != name.lower():
            candidate = candidate.with_name(name)
        if candidate.exists():
            return candidate
    located = shutil.which(name) or shutil.which(name.removesuffix(".exe"))
    if located:
        return Path(located)
    local_app_data = Path(os.environ.get("LOCALAPPDATA", ""))
    package_root = local_app_data / "Microsoft" / "WinGet" / "Packages"
    matches = sorted(package_root.glob(f"FiloSottile.age_*\\age\\{name}"))
    if matches:
        return matches[-1]
    raise DatasetPackageError(
        "Official age tooling is required. Install it with: "
        "winget install --id FiloSottile.age --exact"
    )


def package_files(collection: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(item for item in collection.rglob("*") if item.is_file() and item.name != "package.json"):
        records.append(
            {
                "relative_path": path.relative_to(collection).as_posix(),
                "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size,
            }
        )
    return records


def create_package_manifest(collection: Path, source_session: Path, package_id: str = "") -> dict[str, Any]:
    source_session = source_session.resolve()
    session = read_json(source_session)
    value = {
        "schema_version": PACKAGE_SCHEMA,
        "package_id": package_id or uuid.uuid4().hex,
        "created_at": utc_now(),
        "source_session_id": session.get("session_id"),
        "source_session_sha256": sha256_file(source_session),
        "consent_record": "consent.json",
        "collection_manifest": "manifest.json",
        "encryption": "age-x25519",
        "automatic_upload": False,
        "files": package_files(collection),
    }
    write_json(collection / "package.json", value)
    return value


def encrypt_collection(collection: Path, output: Path, recipient: str, source_session: Path) -> dict[str, Any]:
    if not recipient.startswith("age1"):
        raise DatasetPackageError("A valid age X25519 recipient is required.")
    output = output.resolve()
    if output.suffix.lower() != ".age" or not output.name.lower().endswith(".mimir-dataset.age"):
        raise DatasetPackageError("Encrypted contribution output must end with .mimir-dataset.age")
    collection_manifest = read_json(collection / "manifest.json")
    package = create_package_manifest(
        collection,
        source_session,
        str(collection_manifest.get("package_id") or ""),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mimir-package-") as temporary:
        archive = Path(temporary) / f"{package['package_id']}.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as handle:
            for path in sorted(item for item in collection.rglob("*") if item.is_file()):
                handle.write(path, path.relative_to(collection).as_posix())
        partial = output.with_suffix(output.suffix + ".partial")
        partial.unlink(missing_ok=True)
        result = subprocess.run(
            [str(find_age_executable()), "-r", recipient, "-o", str(partial), str(archive)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if result.returncode != 0:
            partial.unlink(missing_ok=True)
            raise DatasetPackageError(f"age encryption failed: {result.stderr.strip()}")
        partial.replace(output)
    return {
        "package_id": package["package_id"],
        "output": str(output),
        "sha256": sha256_file(output),
        "size_bytes": output.stat().st_size,
        "automatic_upload": False,
    }


def _safe_extract(archive: Path, destination: Path) -> None:
    destination = destination.resolve()
    with zipfile.ZipFile(archive, "r") as handle:
        members = handle.infolist()
        if len(members) > MAX_PACKAGE_FILES:
            raise DatasetPackageError("Contribution package contains too many files.")
        total_size = sum(max(0, member.file_size) for member in members)
        if total_size > MAX_PACKAGE_UNCOMPRESSED_BYTES:
            raise DatasetPackageError("Contribution package exceeds the intake size limit.")
        for member in members:
            target = (destination / member.filename).resolve()
            if target != destination and destination not in target.parents:
                raise DatasetPackageError(f"Unsafe path in contribution package: {member.filename}")
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with handle.open(member, "r") as source, target.open("wb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)


def decrypt_package(package: Path, identity: Path, destination: Path) -> None:
    archive = destination.parent / f"{uuid.uuid4().hex}.zip"
    try:
        result = subprocess.run(
            [str(find_age_executable()), "-d", "-i", str(identity), "-o", str(archive), str(package)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if result.returncode != 0:
            raise DatasetPackageError(f"age decryption failed: {result.stderr.strip()}")
        destination.mkdir(parents=True, exist_ok=False)
        _safe_extract(archive, destination)
    finally:
        archive.unlink(missing_ok=True)


def _verify_package_tree(root: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    package = read_json(root / "package.json")
    consent = read_json(root / str(package.get("consent_record") or "consent.json"))
    manifest = read_json(root / str(package.get("collection_manifest") or "manifest.json"))
    if package.get("schema_version") != PACKAGE_SCHEMA:
        raise DatasetPackageError("Unsupported contribution package schema.")
    if consent.get("schema_version") != "mimir_dataset_consent_v2":
        raise DatasetPackageError("A version 2 clip-by-clip consent receipt is required.")
    if consent.get("rights_confirmed") is not True or consent.get("automatic_upload") is not False:
        raise DatasetPackageError("Contribution consent is missing or invalid.")
    if not str(consent.get("recorded_by") or "").strip():
        raise DatasetPackageError("Consent recorder is missing.")
    if not str(consent.get("rights_basis") or "").strip():
        raise DatasetPackageError("Consent rights basis is missing.")
    if not str(consent.get("permission_reference") or "").strip():
        raise DatasetPackageError("Consent permission reference is missing.")
    records = package.get("files") if isinstance(package.get("files"), list) else []
    if not records:
        raise DatasetPackageError("Contribution package has no file inventory.")
    inventory_paths: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            raise DatasetPackageError("Contribution package inventory is malformed.")
        relative = str(record.get("relative_path") or "")
        path = (root / relative).resolve()
        if root.resolve() not in path.parents:
            raise DatasetPackageError(f"Unsafe inventory path: {relative}")
        if not path.exists() or not path.is_file():
            raise DatasetPackageError(f"Package inventory file is missing: {relative}")
        if sha256_file(path) != str(record.get("sha256") or "").lower():
            raise DatasetPackageError(f"Package inventory hash mismatch: {relative}")
        if path.stat().st_size != int(record.get("size_bytes") or -1):
            raise DatasetPackageError(f"Package inventory size mismatch: {relative}")
        inventory_paths.add(relative)
    actual_paths = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "package.json"
    }
    if inventory_paths != actual_paths:
        raise DatasetPackageError("Contribution package contains unlisted or missing files.")

    media_by_hash: dict[str, Path] = {}
    for item in manifest.get("items", []) if isinstance(manifest.get("items"), list) else []:
        if not isinstance(item, dict):
            continue
        annotation_path = root / str(item.get("annotation") or "")
        annotation = read_json(annotation_path)
        for media in annotation.get("media", []) if isinstance(annotation.get("media"), list) else []:
            if not isinstance(media, dict):
                continue
            media_path = root / str(media.get("relative_path") or "")
            expected = str(media.get("sha256") or "").lower()
            if not media_path.exists() or sha256_file(media_path) != expected:
                raise DatasetPackageError(f"Consented media hash mismatch: {media_path.name}")
            media_by_hash[expected] = media_path
    consent_hashes = {
        str(item.get("sha256") or "").lower()
        for item in consent.get("clips", [])
        if isinstance(item, dict)
    }
    if not media_by_hash or set(media_by_hash) != consent_hashes:
        raise DatasetPackageError("Clip-by-clip consent does not exactly match the packaged media.")

    excluded = exclusion_hashes()
    excluded_present = sorted(set(media_by_hash) & set(excluded))
    if excluded_present:
        record_relative = str(consent.get("independent_permission_record") or "")
        expected = str(consent.get("independent_permission_record_sha256") or "")
        record_path = root / record_relative
        if not record_relative or not record_path.is_file() or sha256_file(record_path) != expected:
            reasons = "; ".join(excluded[value] for value in excluded_present)
            raise DatasetPackageError(
                "Excluded regression footage requires a bundled, independently verified permission record: " + reasons
            )
    return package, consent, manifest


def _read_registry(root: Path) -> dict[str, Any]:
    path = root / "intake_registry.json"
    if not path.exists():
        return {"schema_version": "mimir_intake_registry_v1", "packages": {}, "media": {}, "sources": {}}
    value = read_json(path)
    for key in ("packages", "media", "sources"):
        if not isinstance(value.get(key), dict):
            value[key] = {}
    return value


def _create_cvat_tasks(
    collection: Path,
    manifest: dict[str, Any],
    package_id: str,
    cvat_url: str,
    cvat_token: str,
    cvat_token_file: str,
) -> tuple[int, list[dict[str, Any]]]:
    client = CvatClient(cvat_url, configured_token(cvat_token, cvat_token_file), timeout_sec=900)
    project_result = client.ensure_project()
    project = project_result.get("project") if isinstance(project_result, dict) else {}
    project_id = int(project.get("id")) if isinstance(project, dict) and project.get("id") else None
    if project_id is None:
        raise DatasetPackageError("CVAT project id is missing.")
    existing_tasks = {str(task.get("name") or ""): task for task in client.list_tasks(project_id)}
    tasks: list[dict[str, Any]] = []
    for item in manifest.get("items", []) if isinstance(manifest.get("items"), list) else []:
        if not isinstance(item, dict):
            continue
        annotation = read_json(collection / str(item.get("annotation") or ""))
        media_paths = [
            collection / str(media.get("relative_path") or "")
            for media in annotation.get("media", [])
            if isinstance(media, dict)
        ]
        task_name = f"{item.get('split')} | {package_id[:8]} | {item.get('incident_id')}"
        existing_task = existing_tasks.get(task_name)
        if existing_task and existing_task.get("id"):
            task = {
                "task_id": int(existing_task["id"]),
                "name": task_name,
                "media": [str(path.resolve()) for path in media_paths],
                "reused_after_retry": True,
            }
        else:
            task = client.create_video_task(
                project_id,
                task_name,
                media_paths,
                {"split": str(item.get("split") or "")},
            )
        tasks.append(task)
    return project_id, tasks


def intake_package(
    encrypted_package: Path,
    identity: Path,
    dataset_root: Path,
    cvat_url: str = "",
    cvat_token: str = "",
    cvat_token_file: str = "",
) -> dict[str, Any]:
    encrypted_package = encrypted_package.resolve()
    identity = identity.resolve()
    dataset_root = dataset_root.resolve()
    if not encrypted_package.is_file() or not identity.is_file():
        raise DatasetPackageError("Encrypted package and age identity must both exist.")
    package_digest = sha256_file(encrypted_package)
    dataset_root.mkdir(parents=True, exist_ok=True)
    registry = _read_registry(dataset_root)
    for package_id, record in registry["packages"].items():
        if isinstance(record, dict) and record.get("encrypted_sha256") == package_digest:
            if cvat_url and record.get("cvat_status") != "complete":
                collection = Path(str(record.get("collection_path") or ""))
                manifest = read_json(collection / "manifest.json")
                try:
                    project_id, tasks = _create_cvat_tasks(
                        collection, manifest, package_id, cvat_url, cvat_token, cvat_token_file
                    )
                    record.update(
                        {
                            "cvat_project_id": project_id,
                            "cvat_tasks": tasks,
                            "cvat_status": "complete",
                            "cvat_error": None,
                        }
                    )
                    write_json(collection / "intake_record.json", record)
                    write_json(dataset_root / "intake_registry.json", registry)
                    return {"status": "already_imported_cvat_completed", "package_id": package_id, "record": record}
                except (CvatError, DatasetPackageError) as exc:
                    record.update({"cvat_status": "pending", "cvat_error": str(exc)})
                    write_json(collection / "intake_record.json", record)
                    write_json(dataset_root / "intake_registry.json", registry)
                    return {"status": "already_imported_cvat_pending", "package_id": package_id, "record": record}
            return {"status": "already_imported", "package_id": package_id, "record": record}

    with tempfile.TemporaryDirectory(prefix="mimir-intake-", dir=str(dataset_root.parent)) as temporary:
        extracted = Path(temporary) / "extracted"
        decrypt_package(encrypted_package, identity, extracted)
        package, consent, manifest = _verify_package_tree(extracted)
        package_id = str(package.get("package_id") or "")
        if len(package_id) != 32:
            raise DatasetPackageError("Contribution package id is malformed.")
        existing = registry["packages"].get(package_id)
        if existing:
            if isinstance(existing, dict) and existing.get("encrypted_sha256") == package_digest:
                return {"status": "already_imported", "package_id": package_id, "record": existing}
            raise DatasetPackageError(f"Package id collision: {package_id}")

        source_splits: dict[str, str] = {}
        media_hashes: list[str] = []
        for item in manifest.get("items", []) if isinstance(manifest.get("items"), list) else []:
            if not isinstance(item, dict):
                continue
            source_hash = str(item.get("source_group_hash") or "")
            split = str(item.get("split") or "")
            if split not in {"train", "validation", "test"}:
                raise DatasetPackageError(f"Invalid split for source {source_hash}: {split}")
            previous = registry["sources"].get(source_hash)
            if previous and previous != split:
                raise DatasetPackageError(f"Split leakage: source {source_hash} is already assigned to {previous}.")
            source_splits[source_hash] = split
            record = read_json(extracted / str(item.get("annotation") or ""))
            for media in record.get("media", []) if isinstance(record.get("media"), list) else []:
                if isinstance(media, dict) and media.get("sha256"):
                    digest = str(media["sha256"]).lower()
                    previous_package = registry["media"].get(digest)
                    if previous_package and previous_package != package_id:
                        raise DatasetPackageError(
                            f"Duplicate footage rejected; media already belongs to package {previous_package}."
                        )
                    media_hashes.append(digest)

        collection = dataset_root / "collections" / package_id
        if collection.exists():
            raise DatasetPackageError(f"Collection destination already exists: {collection}")
        staging = dataset_root / "collections" / f".{package_id}.staging"
        if staging.exists():
            shutil.rmtree(staging)
        staging.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(extracted, staging)
        staging.replace(collection)

    splits = sorted(set(source_splits.values()))
    import_record = {
        "schema_version": "mimir_cvat_import_record_v1",
        "package_id": package_id,
        "imported_at": utc_now(),
        "encrypted_package": str(encrypted_package),
        "encrypted_sha256": package_digest,
        "collection_path": str(collection),
        "split": splits[0] if len(splits) == 1 else "mixed",
        "consent_receipt_id": consent.get("receipt_id"),
        "cvat_project_id": None,
        "cvat_tasks": [],
        "cvat_status": "not_requested" if not cvat_url else "pending",
        "cvat_error": None,
        "duplicate_media_rejected": 0,
    }
    write_json(collection / "intake_record.json", import_record)
    registry["packages"][package_id] = import_record
    for digest in media_hashes:
        registry["media"][digest] = package_id
    registry["sources"].update(source_splits)
    registry["updated_at"] = utc_now()
    write_json(dataset_root / "intake_registry.json", registry)
    if cvat_url:
        try:
            project_id, tasks = _create_cvat_tasks(
                collection, manifest, package_id, cvat_url, cvat_token, cvat_token_file
            )
            import_record.update(
                {
                    "cvat_project_id": project_id,
                    "cvat_tasks": tasks,
                    "cvat_status": "complete",
                }
            )
        except (CvatError, DatasetPackageError) as exc:
            # The validated package remains accepted and retryable. CVAT is an
            # annotation destination, not the authority for intake integrity.
            import_record.update({"cvat_status": "pending", "cvat_error": str(exc)})
        registry["packages"][package_id] = import_record
        registry["updated_at"] = utc_now()
        write_json(collection / "intake_record.json", import_record)
        write_json(dataset_root / "intake_registry.json", registry)
    status = "imported_cvat_pending" if import_record.get("cvat_status") == "pending" else "imported"
    return {"status": status, "package_id": package_id, "record": import_record}
