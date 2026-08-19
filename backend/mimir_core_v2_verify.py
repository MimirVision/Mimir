"""Canonical verification entry point for Mimir Core v2.

The quick level is deterministic and suitable for CI. Regression and release
levels add real-fixture and packaging checks on a configured Windows machine.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DEFAULT_REPORT = ROOT / "MimirOutputV2" / "verification_report.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def run_step(name: str, command: list[str]) -> dict:
    started = time.perf_counter()
    print(f"\n[{name}]")
    print(" ".join(command))
    process = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
    if process.stdout:
        print(process.stdout.rstrip())
    if process.stderr:
        print(process.stderr.rstrip(), file=sys.stderr)
    return {
        "name": name,
        "command": command,
        "exit_code": process.returncode,
        "passed": process.returncode == 0,
        "runtime_sec": round(time.perf_counter() - started, 3),
        "stdout_tail": process.stdout[-4000:],
        "stderr_tail": process.stderr[-4000:],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run canonical Mimir Core v2 verification.")
    parser.add_argument("--level", choices=("quick", "regression", "release"), default="quick")
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--skip-package", action="store_true")
    parser.add_argument("--internal", action="store_true", help="Use internal dogfood release gates.")
    return parser


def annotate_failures(steps: list[dict]) -> None:
    """Report a failing step as a GitHub Actions annotation, not just in the log.

    A failing step already prints its stdout and stderr, but reading a job's log
    needs an authenticated token, while check-run annotations are readable by
    anyone on a public repository. backend-verify failed on every run for eleven
    days and all that was visible from outside was "Process completed with exit
    code 1" -- the cause sat in a log nobody could open.

    Annotations are single-line, so newlines are escaped as the workflow command
    syntax requires, and the last lines are kept because a traceback ends with
    the thing that actually broke.
    """

    if not os.environ.get("GITHUB_ACTIONS"):
        return

    for step in steps:
        if step["passed"]:
            continue
        detail = str(step.get("stderr_tail") or step.get("stdout_tail") or "").strip()
        tail = "\n".join(detail.splitlines()[-25:])
        encoded = tail.replace("%", "%25").replace("\r", "").replace("\n", "%0A")
        print(
            f"::error title=Mimir verification failed::"
            f"{step['name']} exited {step['exit_code']}%0A{encoded}"
        )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    started_at = utc_now()
    started = time.perf_counter()
    steps = [
        run_step(
            "core unit and contract tests",
            [
                sys.executable,
                "-m",
                "unittest",
                "discover",
                "-s",
                "mimir_core_v2",
                "-t",
                ".",
                "-p",
                "test_*.py",
            ],
        )
    ]

    if args.level in {"regression", "release"} and all(step["passed"] for step in steps):
        steps.append(
            run_step(
                "real-fixture regression suite",
                [sys.executable, "mimir_core_v2_regression_suite.py"],
            )
        )

    if args.level == "release" and all(step["passed"] for step in steps):
        if not args.skip_package:
            runtime_python = ROOT / ".venv-runtime" / "Scripts" / "python.exe"
            packaging_python = str(runtime_python if runtime_python.exists() else Path(sys.executable))
            steps.append(
                run_step(
                    "self-contained backend sidecars",
                    [packaging_python, "build_backend_exe.py"],
                )
            )
        release_command = [sys.executable, "mimir_core_v2_release_check.py", "--gate-only"]
        if args.internal:
            release_command.append("--internal")
        steps.append(run_step("release evidence", release_command))

    passed = bool(steps) and all(step["passed"] for step in steps)
    report = {
        "schema_version": "mimir_verification_report_v1",
        "level": args.level,
        "started_at": started_at,
        "completed_at": utc_now(),
        "runtime_sec": round(time.perf_counter() - started, 3),
        "passed": passed,
        "steps": steps,
    }
    report_path = Path(args.report).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = report_path.with_suffix(report_path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    temporary.replace(report_path)

    annotate_failures(steps)

    print("\nMIMIR CORE V2 VERIFICATION " + ("PASSED" if passed else "FAILED"))
    print(f"report: {report_path}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
