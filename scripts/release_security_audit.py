"""Run dependency, secret, and packaged-content checks for a release candidate."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SECRET_PATTERNS = {
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "aws_access_key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "credential_assignment": re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*['\"][^'\"]{12,}['\"]"),
}
TEXT_SUFFIXES = {".py", ".rs", ".ts", ".tsx", ".js", ".json", ".toml", ".md", ".ps1", ".html", ".css"}


def run_json(command: list[str], cwd: Path) -> tuple[bool, Any, str]:
    executable = shutil.which(command[0])
    if executable:
        command = [executable, *command[1:]]
        if executable.lower().endswith((".cmd", ".bat")):
            command = ["cmd", "/d", "/c", *command]
    try:
        result = subprocess.run(command, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as exc:
        return False, {}, str(exc)
    try:
        payload = json.loads(result.stdout) if result.stdout.strip() else {}
    except json.JSONDecodeError:
        payload = {}
    return result.returncode == 0, payload, result.stderr.strip()


def tracked_files(root: Path) -> list[str]:
    result = subprocess.run(["git", "-C", str(root), "ls-files"], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return result.stdout.splitlines() if result.returncode == 0 else []


def scan_secrets(root: Path, files: list[str]) -> list[dict[str, str]]:
    findings = []
    for relative in files:
        path = root / relative
        if path.suffix.lower() not in TEXT_SUFFIXES or path.name in {"package-lock.json", "Cargo.lock"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for name, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                findings.append({"type": name, "file": relative})
    return findings


def forbidden_content(files: list[str]) -> list[str]:
    prefixes = (".venv/", "Test/", "beta-smoke-test/", "MimirOutput/", "MimirOutputV2/", "Frames/", "dist/", "dist_backend/", "dist-release/", "build/", "build_backend/", "src-tauri/resources/mimir-backend/_internal/")
    suffixes = (".mp4", ".mov", ".avi", ".pt", ".onnx")
    findings = []
    for raw in files:
        path = raw.replace("\\", "/")
        if path.startswith(prefixes) or path.lower().endswith(suffixes):
            findings.append(path)
    return findings


def scan_packaged_runtimes(frontend: Path) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    markers = (b"ultralytics", b"yolo11n.pt", b"yolov8n.pt", b"torchvision")
    runtime_dir = frontend / "src-tauri" / "resources" / "mimir-backend"
    packaged_names = (
        "mimir-core-v2-scan.exe",
        "mimir-core-v2-actions.exe",
        "mimir-core-v2-release-check.exe",
    )
    for name in packaged_names:
        path = runtime_dir / name
        if not path.exists():
            findings.append({"file": str(path), "marker": "missing_required_runtime"})
            continue
        try:
            payload = path.read_bytes().lower()
        except OSError:
            continue
        for marker in markers:
            if marker in payload:
                findings.append({"file": str(path), "marker": marker.decode("ascii")})
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frontend-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--backend-root", default="C:\\Mimir_Backend")
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    frontend = Path(args.frontend_root).resolve()
    backend = Path(args.backend_root).resolve()
    output = Path(args.output).resolve() if args.output else frontend / "release_assets" / "security_scan_report.json"

    npm_ok, npm, npm_error = run_json(["npm", "audit", "--json"], frontend)
    npm_vulnerabilities = int(((npm.get("metadata") or {}).get("vulnerabilities") or {}).get("total") or 0) if isinstance(npm, dict) else -1
    npm_passed = npm_ok and npm_vulnerabilities == 0
    cargo_ok, cargo, cargo_error = run_json(["cargo", "audit", "--json"], frontend / "src-tauri")
    python = backend / ".venv-runtime" / "Scripts" / "python.exe"
    if not python.exists():
        python = Path("python")
    requirements = backend / "requirements-core-v2.txt"
    pip_ok, pip_payload, pip_error = run_json(
        [str(python), "-m", "pip_audit", "-r", str(requirements), "-f", "json"],
        backend,
    )

    front_files = tracked_files(frontend)
    back_files = tracked_files(backend)
    secrets = scan_secrets(frontend, front_files) + scan_secrets(backend, back_files)
    forbidden = forbidden_content(front_files) + forbidden_content(back_files)
    runtime_findings = scan_packaged_runtimes(frontend)
    passed = npm_passed and cargo_ok and pip_ok and not secrets and not forbidden and not runtime_findings
    report = {
        "schema_version": "mimir_security_scan_v1",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "passed": passed,
        "summary": "all security checks passed" if passed else "one or more release security checks are incomplete or failed",
        "npm_audit": {"passed": npm_passed, "vulnerabilities": npm_vulnerabilities, "error": npm_error},
        "cargo_audit": {"passed": cargo_ok, "error": cargo_error, "report": cargo},
        "pip_audit": {"passed": pip_ok, "error": pip_error, "report": pip_payload},
        "secret_scan": {"passed": not secrets, "findings": secrets},
        "packaged_content": {
            "passed": not forbidden and not runtime_findings,
            "tracked_findings_count": len(forbidden),
            "tracked_examples": forbidden[:20],
            "runtime_findings": runtime_findings,
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Security report: {output}")
    print("SECURITY AUDIT PASSED" if passed else "SECURITY AUDIT BLOCKED")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
