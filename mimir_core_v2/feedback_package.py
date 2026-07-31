"""Encrypted feedback package and intake primitives.

Deliberately parallel to, but independent from, `dataset_package.py`.
`dataset_package.py`'s `encrypt_collection()` is tightly coupled to the full
contribution shape -- it unconditionally reads `manifest.json` and a
`source_session` file via `create_package_manifest()`. Feedback has neither,
so building on top of that function would either force feedback through a
dataset-shaped manifest it doesn't need, or grow `encrypt_collection()` with
conditionals for a shape it was never designed for. A parallel module keeps
the two independent: a change to how contributions are packaged cannot
silently change how feedback is packaged, and vice versa.

What IS reused from `dataset_package.py`: the purely mechanical, security-
sensitive pieces that have nothing dataset-specific about them --
`find_age_executable`, `sha256_file`, `read_json`/`write_json`,
`package_files`, and `_safe_extract` (the zip-slip / path-traversal guard).
That last one especially should not be duplicated: it is a security
boundary, and two copies of a zip-slip defense is a way for one of them to
quietly bitrot into a vulnerability. What is NOT reused is anything shaped
around manifest/consent/source_session, since that shape doesn't apply here.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Any

from .dataset_package import (
    DatasetPackageError,
    _safe_extract,
    _SUBPROCESS_NO_WINDOW_FLAGS,
    find_age_executable,
    package_files,
    read_json,
    sha256_file,
    utc_now,
    write_json,
)

FEEDBACK_PACKAGE_SCHEMA = "mimir_feedback_package_v1"


def build_feedback_package(feedback: dict[str, Any], video_path: Path | None, staging_root: Path) -> Path:
    """Stage feedback.json (+ an optional video copy) into a fresh folder
    under `staging_root`, write package.json, and return the staged
    collection path -- ready for `encrypt_feedback_collection`.

    No manifest.json, no consent.json, no source_session. That absence is
    the whole point of this module existing separately from dataset_package.
    """

    package_id = uuid.uuid4().hex
    collection = staging_root / package_id
    collection.mkdir(parents=True, exist_ok=False)

    write_json(collection / "feedback.json", feedback)

    if video_path is not None:
        if not video_path.is_file():
            raise DatasetPackageError(f"Feedback video does not exist: {video_path}")
        video_dir = collection / "video"
        video_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(video_path, video_dir / video_path.name)

    package = {
        "schema_version": FEEDBACK_PACKAGE_SCHEMA,
        "package_id": package_id,
        "created_at": utc_now(),
        "encryption": "age-x25519",
        "automatic_upload": False,
        "files": package_files(collection),
    }
    write_json(collection / "package.json", package)
    return collection


def encrypt_feedback_collection(collection: Path, output: Path, recipient: str) -> dict[str, Any]:
    """Zip `collection` and encrypt it to `output` with age.

    Same shell-to-age.exe / write-to-a-.partial-then-replace pattern as
    `dataset_package.encrypt_collection`, kept structurally identical so the
    two are easy to compare, but with no source_session parameter -- feedback
    packages have nothing analogous to package against.
    """

    if not recipient.startswith("age1"):
        raise DatasetPackageError("A valid age X25519 recipient is required.")
    output = output.resolve()
    if output.suffix.lower() != ".age" or not output.name.lower().endswith(".mimir-feedback.age"):
        raise DatasetPackageError("Encrypted feedback output must end with .mimir-feedback.age")

    package = read_json(collection / "package.json")
    if package.get("schema_version") != FEEDBACK_PACKAGE_SCHEMA:
        raise DatasetPackageError("Collection does not look like a staged feedback package.")

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mimir-feedback-package-") as temporary:
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
            **_SUBPROCESS_NO_WINDOW_FLAGS,
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


def decrypt_feedback_package(package: Path, identity: Path, destination: Path) -> None:
    """Mirror of `dataset_package.decrypt_package`, against the feedback shape."""

    archive = destination.parent / f"{uuid.uuid4().hex}.zip"
    try:
        result = subprocess.run(
            [str(find_age_executable()), "-d", "-i", str(identity), "-o", str(archive), str(package)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **_SUBPROCESS_NO_WINDOW_FLAGS,
        )
        if result.returncode != 0:
            raise DatasetPackageError(f"age decryption failed: {result.stderr.strip()}")
        destination.mkdir(parents=True, exist_ok=False)
        _safe_extract(archive, destination)
    finally:
        archive.unlink(missing_ok=True)


def _verify_feedback_package_tree(root: Path) -> dict[str, Any]:
    """Lightweight verification: schema tag + exact file-inventory hash match.

    Deliberately does not check consent, media-by-hash cross-references, or
    split leakage -- none of that applies to feedback. This is the intake-side
    analogue of dataset_package._verify_package_tree, simplified to match
    what a feedback package actually contains.
    """

    package = read_json(root / "package.json")
    if package.get("schema_version") != FEEDBACK_PACKAGE_SCHEMA:
        raise DatasetPackageError("Unsupported feedback package schema.")
    records = package.get("files") if isinstance(package.get("files"), list) else []
    if not records:
        raise DatasetPackageError("Feedback package has no file inventory.")

    inventory_paths: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            raise DatasetPackageError("Feedback package inventory is malformed.")
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
        raise DatasetPackageError("Feedback package contains unlisted or missing files.")

    if not (root / "feedback.json").is_file():
        raise DatasetPackageError("Feedback package is missing feedback.json.")

    return package


def _read_feedback_registry(feedback_root: Path) -> dict[str, Any]:
    path = feedback_root / "feedback_registry.json"
    if not path.exists():
        return {"schema_version": "mimir_feedback_registry_v1", "packages": {}}
    value = read_json(path)
    if not isinstance(value.get("packages"), dict):
        value["packages"] = {}
    return value


def intake_feedback_package(encrypted_package: Path, identity: Path, feedback_root: Path) -> dict[str, Any]:
    """Decrypt, verify, and file a feedback package under `feedback_root`.

    Mirrors `dataset_package.intake_package`'s dedupe-by-content-hash and
    atomic-move-into-place pattern, without any of the dataset-specific
    split/media-hash/CVAT bookkeeping -- feedback has no annotation pipeline
    to feed.
    """

    encrypted_package = encrypted_package.resolve()
    identity = identity.resolve()
    feedback_root = feedback_root.resolve()
    if not encrypted_package.is_file() or not identity.is_file():
        raise DatasetPackageError("Encrypted package and age identity must both exist.")

    package_digest = sha256_file(encrypted_package)
    feedback_root.mkdir(parents=True, exist_ok=True)
    registry = _read_feedback_registry(feedback_root)
    for package_id, record in registry["packages"].items():
        if isinstance(record, dict) and record.get("encrypted_sha256") == package_digest:
            return {"status": "already_imported", "package_id": package_id, "record": record}

    with tempfile.TemporaryDirectory(prefix="mimir-feedback-intake-", dir=str(feedback_root.parent)) as temporary:
        extracted = Path(temporary) / "extracted"
        decrypt_feedback_package(encrypted_package, identity, extracted)
        package = _verify_feedback_package_tree(extracted)
        package_id = str(package.get("package_id") or "")
        if len(package_id) != 32:
            raise DatasetPackageError("Feedback package id is malformed.")
        existing = registry["packages"].get(package_id)
        if existing:
            if isinstance(existing, dict) and existing.get("encrypted_sha256") == package_digest:
                return {"status": "already_imported", "package_id": package_id, "record": existing}
            raise DatasetPackageError(f"Package id collision: {package_id}")

        destination = feedback_root / package_id
        if destination.exists():
            raise DatasetPackageError(f"Feedback destination already exists: {destination}")
        staging = feedback_root / f".{package_id}.staging"
        if staging.exists():
            shutil.rmtree(staging)
        staging.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(extracted, staging)
        staging.replace(destination)

    record = {
        "schema_version": "mimir_feedback_intake_record_v1",
        "package_id": package_id,
        "imported_at": utc_now(),
        "encrypted_package": str(encrypted_package),
        "encrypted_sha256": package_digest,
        "feedback_path": str(destination),
    }
    write_json(destination / "intake_record.json", record)
    registry["packages"][package_id] = record
    registry["updated_at"] = utc_now()
    write_json(feedback_root / "feedback_registry.json", registry)
    return {"status": "imported", "package_id": package_id, "record": record}
