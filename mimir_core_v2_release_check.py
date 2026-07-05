"""Release validation for Mimir Core v2.

This script only runs validation commands. It does not modify scanner behavior.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SESSION_PATH = ROOT / "MimirOutputV2" / "latest_session.json"
TEST_INPUT = r"C:\mimir\test"


class ReleaseCheckFailed(Exception):
    pass


def command_text(args: list[str]) -> str:
    return " ".join(f'"{arg}"' if " " in str(arg) else str(arg) for arg in args)


def run_required(args: list[str], label: str) -> None:
    print()
    print(f"> {command_text(args)}")
    result = subprocess.run(args, cwd=ROOT)
    if result.returncode != 0:
        raise ReleaseCheckFailed(f"{label} failed with exit code {result.returncode}")


def py_compile_v2_modules() -> None:
    module_paths = sorted((ROOT / "mimir_core_v2").glob("*.py"))
    if not module_paths:
        raise ReleaseCheckFailed("No mimir_core_v2 modules found")

    for module_path in module_paths:
        relative = str(module_path.relative_to(ROOT))
        run_required([sys.executable, "-m", "py_compile", relative], f"compile {relative}")

    run_required([sys.executable, "-m", "py_compile", "mimir_core_v2_scan.py"], "compile mimir_core_v2_scan.py")


def read_session() -> dict:
    if not SESSION_PATH.exists():
        raise ReleaseCheckFailed(f"latest_session.json missing: {SESSION_PATH}")

    try:
        with SESSION_PATH.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseCheckFailed(f"Could not read latest_session.json: {exc}") from exc

    if not isinstance(data, dict):
        raise ReleaseCheckFailed("latest_session.json is not a JSON object")

    return data


def print_summary(session: dict, grouping_status: str, benchmark_status: str, runtime_sec: float) -> None:
    incidents = session.get("incidents") if isinstance(session.get("incidents"), list) else []
    print()
    print("Mimir Core v2 release summary")
    print("=============================")
    print(f"grouping status: {grouping_status}")
    print(f"incidents created: {len(incidents)}")
    print(f"important: {session.get('important', 'not available')}")
    print(f"review: {session.get('review', 'not available')}")
    print(f"ignore: {session.get('ignore', 'not available')}")
    print(f"benchmark status: {benchmark_status}")
    print(f"runtime: {runtime_sec:.1f} sec")


def main() -> int:
    started_at = time.perf_counter()
    grouping_status = "not run"
    benchmark_status = "not run"

    try:
        print("Mimir Core v2 release check")
        print(f"Backend root: {ROOT}")

        py_compile_v2_modules()
        run_required([sys.executable, "-m", "mimir_core_v2.test_resolver"], "resolver tests")
        run_required(
            [
                sys.executable,
                "mimir_core_v2_scan.py",
                "--input",
                TEST_INPUT,
                "--mode",
                "balanced",
                "--vlm",
                "qwen2.5vl:7b",
            ],
            "v2 scan",
        )
        run_required(
            [sys.executable, "-m", "mimir_core_v2.test_grouping", "--input", TEST_INPUT],
            "grouping test",
        )
        grouping_status = "passed"
        run_required([sys.executable, "-m", "mimir_core_v2.benchmark"], "benchmark")
        benchmark_status = "passed"

        session = read_session()
        print_summary(session, grouping_status, benchmark_status, time.perf_counter() - started_at)
        print()
        print("MIMIR CORE V2 RELEASE CHECK PASSED")
        return 0
    except ReleaseCheckFailed as exc:
        print()
        print(f"Release check error: {exc}")
        session = {}
        try:
            session = read_session()
        except ReleaseCheckFailed:
            pass
        print_summary(session, grouping_status, benchmark_status, time.perf_counter() - started_at)
        print()
        print("MIMIR CORE V2 RELEASE CHECK FAILED")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

