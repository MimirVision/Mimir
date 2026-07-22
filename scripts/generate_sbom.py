"""Generate a deterministic CycloneDX inventory from checked-in dependency files."""

from __future__ import annotations

import argparse
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def npm_components(lock_path: Path) -> list[dict[str, Any]]:
    lock = read_json(lock_path)
    components = []
    for package_path, data in sorted((lock.get("packages") or {}).items()):
        if not package_path or not isinstance(data, dict):
            continue
        name = str(data.get("name") or package_path.rsplit("node_modules/", 1)[-1])
        version = str(data.get("version") or "unknown")
        components.append({"type": "library", "name": name, "version": version, "purl": f"pkg:npm/{name}@{version}"})
    return components


def cargo_components(lock_path: Path) -> list[dict[str, Any]]:
    try:
        text = lock_path.read_text(encoding="utf-8")
    except OSError:
        return []
    components = []
    for block in text.split("[[package]]")[1:]:
        name_match = re.search(r'^name\s*=\s*"([^"]+)"', block, re.MULTILINE)
        version_match = re.search(r'^version\s*=\s*"([^"]+)"', block, re.MULTILINE)
        if name_match and version_match:
            name, version = name_match.group(1), version_match.group(1)
            components.append({"type": "library", "name": name, "version": version, "purl": f"pkg:cargo/{name}@{version}"})
    return components


def python_components(requirements: Path) -> list[dict[str, Any]]:
    try:
        lines = requirements.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    components = []
    for line in lines:
        value = line.split("#", 1)[0].strip()
        if not value or value.startswith("-"):
            continue
        match = re.match(r"([A-Za-z0-9_.-]+)\s*(?:==|>=|~=|<=|>|<)?\s*([^;\s]+)?", value)
        if match:
            name, version = match.group(1), match.group(2) or "unresolved"
            components.append({"type": "library", "name": name, "version": version, "purl": f"pkg:pypi/{name}@{version}"})
    return components


def model_components(backend_root: Path) -> list[dict[str, Any]]:
    manifest = read_json(backend_root / "mimir_core_v2" / "model_manifest.json")
    if not manifest:
        return []
    return [
        {
            "type": "machine-learning-model",
            "name": str(manifest.get("detector_id") or "unknown_detector"),
            "version": str(manifest.get("manifest_version") or "unknown"),
            "licenses": [{"license": {"id": str(manifest.get("license") or "NOASSERTION")}}],
            "properties": [
                {"name": "mimir:release_blocker", "value": str(bool(manifest.get("release_blocker"))).lower()},
                {"name": "mimir:runtime", "value": str(manifest.get("runtime") or "unknown")},
            ],
        }
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frontend-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--backend-root", default="C:\\Mimir_Backend")
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    frontend = Path(args.frontend_root).resolve()
    backend = Path(args.backend_root).resolve()
    output = Path(args.output).resolve() if args.output else frontend / "release_assets" / "sbom.cdx.json"
    components = npm_components(frontend / "package-lock.json")
    components += cargo_components(frontend / "src-tauri" / "Cargo.lock")
    requirements = backend / "requirements-core-v2.txt"
    if not requirements.exists():
        requirements = backend / "requirements.txt"
    components += python_components(requirements)
    components += model_components(backend)
    unique = {(item["type"], item["name"], item["version"]): item for item in components}
    document = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, 'mimir-free-beta-sbom')}",
        "version": 1,
        "metadata": {
            "timestamp": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "component": {"type": "application", "name": "Mimir", "version": "0.5.0-beta.1"},
            "properties": [{"name": "mimir:release_channel", "value": "free-invite-only-beta"}],
        },
        "components": sorted(unique.values(), key=lambda item: (item["type"], item["name"], item["version"])),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    print(f"SBOM written: {output} ({len(document['components'])} components)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
