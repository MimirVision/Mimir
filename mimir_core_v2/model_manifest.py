"""Runtime model provenance for sessions and release checks."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


MANIFEST_PATH = Path(__file__).with_name("model_manifest.json")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def active_detector_manifest() -> dict:
    try:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        manifest = {
            "manifest_version": "mimir_model_manifest_v1",
            "detector_id": "unknown",
            "release_blocker": True,
            "commercial_distribution_approved": False,
        }
    if not isinstance(manifest, dict):
        manifest = {}
    backend_root = Path(__file__).resolve().parents[1]
    expected_models = {
        str(item.get("filename")): item
        for item in manifest.get("models", [])
        if isinstance(item, dict) and item.get("filename")
    } if isinstance(manifest.get("models"), list) else {}
    model_files: list[dict] = []
    for filename in manifest.get("model_files", []) if isinstance(manifest.get("model_files"), list) else []:
        path = backend_root / str(filename)
        record = {"filename": str(filename), "exists": path.exists(), "sha256": "", "size_bytes": 0}
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
    manifest["resolved_model_files"] = model_files
    return manifest
