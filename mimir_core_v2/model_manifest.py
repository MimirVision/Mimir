"""Runtime model provenance for sessions and release checks.

The detector model normally ships bundled inside the packaged backend exe
(baked in at build time via PyInstaller --add-data). To let a validated model
improvement reach users without a full app reinstall, this module also checks
an optional override directory (MIMIR_MODEL_OVERRIDE_DIR) for a newer,
independently-installed model_manifest.json + model file. The override is
only trusted if every model file it declares exists on disk with a matching
sha256 -- a partially-installed or corrupted override is never used, and the
bundled model is always the safe fallback.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


MANIFEST_PATH = Path(__file__).with_name("model_manifest.json")
OVERRIDE_DIR_ENV = "MIMIR_MODEL_OVERRIDE_DIR"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_model_files(manifest: dict, root: Path) -> list[dict]:
    expected_models = {
        str(item.get("filename")): item
        for item in manifest.get("models", [])
        if isinstance(item, dict) and item.get("filename")
    } if isinstance(manifest.get("models"), list) else {}
    model_files: list[dict] = []
    for filename in manifest.get("model_files", []) if isinstance(manifest.get("model_files"), list) else []:
        path = root / str(filename)
        record = {
            "filename": str(filename),
            "path": str(path),
            "exists": path.exists(),
            "sha256": "",
            "size_bytes": 0,
        }
        if path.exists() and path.is_file():
            try:
                record["sha256"] = _sha256(path)
                record["size_bytes"] = path.stat().st_size
            except OSError:
                pass
        expected = expected_models.get(str(filename), {})
        record["expected_sha256"] = str(expected.get("sha256") or "")
        record["checksum_matches"] = bool(record["expected_sha256"] and record["sha256"] == record["expected_sha256"])
        model_files.append(record)
    return model_files


def _load_manifest(path: Path) -> dict | None:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return manifest if isinstance(manifest, dict) else None


def _override_manifest() -> dict | None:
    override_dir = os.environ.get(OVERRIDE_DIR_ENV, "").strip()
    if not override_dir:
        return None
    override_root = Path(override_dir)
    manifest = _load_manifest(override_root / "model_manifest.json")
    if manifest is None:
        return None
    resolved = _resolve_model_files(manifest, override_root)
    if not resolved or not all(item["exists"] and item["checksum_matches"] for item in resolved):
        return None
    manifest["resolved_model_files"] = resolved
    manifest["_model_source"] = "override"
    manifest["_model_source_dir"] = str(override_root)
    return manifest


def active_detector_manifest() -> dict:
    override = _override_manifest()
    if override is not None:
        return override

    manifest = _load_manifest(MANIFEST_PATH)
    if manifest is None:
        manifest = {
            "manifest_version": "mimir_model_manifest_v1",
            "detector_id": "unknown",
            "release_blocker": True,
            "commercial_distribution_approved": False,
        }
    backend_root = Path(__file__).resolve().parents[1]
    manifest["resolved_model_files"] = _resolve_model_files(manifest, backend_root)
    manifest["_model_source"] = "bundled"
    manifest["_model_source_dir"] = str(backend_root)
    return manifest
