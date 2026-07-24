"""Install a validated detector model update without a full app reinstall.

A "model update package" is a small directory (or the extracted contents of
one) containing a model_manifest.json in the same shape as the bundled
mimir_core_v2/model_manifest.json, plus the model file(s) it declares. This
module verifies the package is well-formed and checksum-correct, then
atomically installs it into an override directory that model_manifest.py
already knows to check ahead of the bundled model (see OVERRIDE_DIR_ENV
there). Nothing here talks to a network -- it only validates and moves files
that are already on disk, so it works the same way whether the package
arrived via a manual download, a USB drive, or a future auto-updater.

Run directly for CLI/sidecar use:
    python -m mimir_core_v2.model_update install --package <dir> --target <dir>
    python -m mimir_core_v2.model_update status --target <dir>
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import uuid
from pathlib import Path

from .model_manifest import OVERRIDE_DIR_ENV, _resolve_model_files, _sha256  # noqa: F401 (re-exported for callers/tests)


class ModelPackageError(ValueError):
    """The candidate package failed validation and was not installed."""


REQUIRED_MANIFEST_FIELDS = (
    "manifest_version",
    "detector_id",
    "detector_version",
    "runtime",
    "architecture",
    "model_files",
    "models",
    "license",
)


def _load_package_manifest(package_dir: Path) -> dict:
    manifest_path = package_dir / "model_manifest.json"
    if not manifest_path.is_file():
        raise ModelPackageError(f"model_manifest.json not found in {package_dir}")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ModelPackageError(f"model_manifest.json is not valid JSON: {exc}") from exc

    if not isinstance(manifest, dict):
        raise ModelPackageError("model_manifest.json must contain a JSON object")

    missing = [field for field in REQUIRED_MANIFEST_FIELDS if not manifest.get(field)]
    if missing:
        raise ModelPackageError(f"model_manifest.json is missing required fields: {', '.join(missing)}")

    if manifest.get("manifest_version") != "mimir_model_manifest_v1":
        raise ModelPackageError(f"unsupported manifest_version: {manifest.get('manifest_version')!r}")

    if manifest.get("release_blocker") is True:
        raise ModelPackageError("package manifest marks itself release_blocker=true and cannot be installed")

    if manifest.get("commercial_distribution_approved") is not True:
        raise ModelPackageError("package manifest does not set commercial_distribution_approved=true")

    if not isinstance(manifest.get("model_files"), list) or not manifest["model_files"]:
        raise ModelPackageError("model_manifest.json has no model_files entries")

    return manifest


def validate_package(package_dir: Path) -> dict:
    """Validate a candidate model package. Raises ModelPackageError on any problem.

    Returns the parsed manifest with resolved_model_files pointing at the
    package_dir on success -- every declared file existed and matched its
    declared sha256.
    """
    manifest = _load_package_manifest(package_dir)
    resolved = _resolve_model_files(manifest, package_dir)

    if not resolved:
        raise ModelPackageError("no model files resolved from package manifest")

    for item in resolved:
        if not item["exists"]:
            raise ModelPackageError(f"declared model file missing from package: {item['filename']}")
        if not item["expected_sha256"]:
            raise ModelPackageError(f"model_manifest.json has no expected sha256 for {item['filename']}")
        if not item["checksum_matches"]:
            raise ModelPackageError(
                f"checksum mismatch for {item['filename']}: "
                f"expected {item['expected_sha256']}, got {item['sha256']}"
            )

    license_file = manifest.get("license_file")
    if license_file and not (package_dir / str(license_file)).is_file():
        raise ModelPackageError(f"declared license_file missing from package: {license_file}")

    manifest = dict(manifest)
    manifest["resolved_model_files"] = resolved
    return manifest


def _atomic_replace_dir(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if target.exists():
        backup = target.parent / f".{target.name}.bak-{uuid.uuid4().hex}"
        target.replace(backup)
    try:
        source.replace(target)
    except OSError:
        if backup is not None:
            backup.replace(target)
        raise
    else:
        if backup is not None:
            shutil.rmtree(backup, ignore_errors=True)


def install_model_package(package_dir: Path, target_dir: Path) -> dict:
    """Validate then atomically install a model package into target_dir.

    On any validation failure, target_dir is left untouched (so the app keeps
    using whatever it was already using -- either a prior override or the
    bundled model). Raises ModelPackageError if the package is invalid.
    """
    manifest = validate_package(package_dir)

    staging = target_dir.parent / f".{target_dir.name}.staging-{uuid.uuid4().hex}"
    staging.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(package_dir / "model_manifest.json", staging / "model_manifest.json")
        for item in manifest["resolved_model_files"]:
            source_path = package_dir / item["filename"]
            dest_path = staging / item["filename"]
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, dest_path)
        license_file = manifest.get("license_file")
        if license_file:
            dest_license = staging / str(license_file)
            dest_license.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(package_dir / str(license_file), dest_license)

        staged_manifest = validate_package(staging)
        _atomic_replace_dir(staging, target_dir)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    return {
        "ok": True,
        "detector_id": staged_manifest.get("detector_id"),
        "detector_version": staged_manifest.get("detector_version"),
        "installed_to": str(target_dir),
    }


def describe_active_model(target_dir: Path) -> dict:
    """Report what model an override directory currently holds, without
    installing anything. Used to show "current model" status in the UI."""
    manifest_path = target_dir / "model_manifest.json"
    if not manifest_path.is_file():
        return {"installed": False, "target_dir": str(target_dir)}

    try:
        manifest = validate_package(target_dir)
    except ModelPackageError as exc:
        return {"installed": False, "target_dir": str(target_dir), "error": str(exc)}

    return {
        "installed": True,
        "target_dir": str(target_dir),
        "detector_id": manifest.get("detector_id"),
        "detector_version": manifest.get("detector_version"),
    }


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    install_parser = subparsers.add_parser("install", help="Validate and install a model update package")
    install_parser.add_argument("--package", required=True, type=Path)
    install_parser.add_argument("--target", required=True, type=Path)

    status_parser = subparsers.add_parser("status", help="Report the model currently installed in an override dir")
    status_parser.add_argument("--target", required=True, type=Path)

    args = parser.parse_args(argv)

    if args.command == "install":
        try:
            result = install_model_package(args.package, args.target)
        except ModelPackageError as exc:
            print(json.dumps({"ok": False, "message": str(exc)}))
            return 1
        print(json.dumps(result))
        return 0

    if args.command == "status":
        print(json.dumps(describe_active_model(args.target)))
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(_main())
