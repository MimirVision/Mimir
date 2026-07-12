"""Build Windows executables for Mimir Core v2 backend entrypoints.

This script is packaging-only. It compiles existing backend entrypoints and
then uses PyInstaller to produce beta executables under dist_backend.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DIST_DIR = ROOT / "dist_backend"
BUILD_DIR = ROOT / "build_backend"
ENTRYPOINT_DIR = BUILD_DIR / "entrypoints"
SPEC_DIR = BUILD_DIR / "specs"
WORK_DIR = BUILD_DIR / "work"

REQUIRED_FILES = [
    ROOT / "mimir_core_v2_scan.py",
    ROOT / "mimir_core_v2_actions.py",
]

OPTIONAL_FILES = [
    ROOT / "mimir_core_v2_release_check.py",
]

CORE_MODULES = [
    ROOT / "mimir_core_v2" / "__init__.py",
    ROOT / "mimir_core_v2" / "cli.py",
    ROOT / "mimir_core_v2" / "source_discovery.py",
    ROOT / "mimir_core_v2" / "event_grouping.py",
    ROOT / "mimir_core_v2" / "frame_sampler.py",
    ROOT / "mimir_core_v2" / "evidence_extractor.py",
    ROOT / "mimir_core_v2" / "thumbnailer.py",
    ROOT / "mimir_core_v2" / "ai_reviewer.py",
    ROOT / "mimir_core_v2" / "severity_resolver.py",
    ROOT / "mimir_core_v2" / "output_writer.py",
    ROOT / "mimir_core_v2" / "validators.py",
]


class PackagingError(Exception):
    pass


def command_text(args: list[str]) -> str:
    return " ".join(f'"{arg}"' if " " in str(arg) else str(arg) for arg in args)


def run(args: list[str], label: str) -> None:
    print()
    print(f"> {command_text(args)}")
    result = subprocess.run(args, cwd=ROOT)
    if result.returncode != 0:
        raise PackagingError(f"{label} failed with exit code {result.returncode}")


def verify_files() -> None:
    missing = [path for path in REQUIRED_FILES if not path.exists()]
    if missing:
        missing_text = "\n".join(f"- {path}" for path in missing)
        raise PackagingError(f"Required packaging files are missing:\n{missing_text}")


def verify_pyinstaller() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--version"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if result.returncode != 0:
        print(result.stdout or "")
        raise PackagingError(
            "PyInstaller is not installed in this Python environment.\n"
            "Install it in the backend venv with:\n"
            "python -m pip install pyinstaller"
        )
    print(f"PyInstaller {result.stdout.strip()} detected")


def compile_checks() -> None:
    compile_targets = list(REQUIRED_FILES)
    compile_targets.extend(path for path in OPTIONAL_FILES if path.exists())
    compile_targets.extend(path for path in CORE_MODULES if path.exists())

    for target in compile_targets:
        run([sys.executable, "-m", "py_compile", str(target)], f"compile {target.name}")


def write_script_wrapper(wrapper_path: Path, source_script: Path) -> None:
    """Write a packaging-only wrapper that executes a backend source script.

    The wrapper lets one-file executables keep beta output paths anchored at
    C:\\Mimir_Backend without changing the existing backend scripts.
    """

    wrapper_path.parent.mkdir(parents=True, exist_ok=True)
    escaped_root = str(ROOT).replace("\\", "\\\\")
    script_name = source_script.name
    wrapper_path.write_text(
        f'''"""PyInstaller wrapper for {script_name}."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_PACKAGING_IMPORTS = (
    argparse,
    json,
    os,
    shutil,
    subprocess,
    sys,
    time,
    datetime,
    timezone,
    Path,
    Any,
)


BACKEND_ROOT = Path(os.environ.get("MIMIR_BACKEND_ROOT", r"{escaped_root}"))
SOURCE_SCRIPT = BACKEND_ROOT / "{script_name}"

if not SOURCE_SCRIPT.exists():
    raise SystemExit(f"Backend source script not found: {{SOURCE_SCRIPT}}")

sys.path.insert(0, str(BACKEND_ROOT))
code = compile(SOURCE_SCRIPT.read_text(encoding="utf-8"), str(SOURCE_SCRIPT), "exec")
globals_dict = {{
    "__name__": "__main__",
    "__file__": str(SOURCE_SCRIPT),
    "__package__": None,
    "__cached__": None,
}}
exec(code, globals_dict)
''',
        encoding="utf-8",
    )


def pyinstaller_command(name: str, entrypoint: Path) -> list[str]:
    return [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--name",
        name,
        "--distpath",
        str(DIST_DIR),
        "--workpath",
        str(WORK_DIR / name),
        "--specpath",
        str(SPEC_DIR),
        str(entrypoint),
    ]


def build_executable(name: str, entrypoint: Path) -> None:
    run(pyinstaller_command(name, entrypoint), f"build {name}.exe")
    exe_path = DIST_DIR / f"{name}.exe"
    if not exe_path.exists():
        raise PackagingError(f"Expected executable was not created: {exe_path}")
    print(f"created: {exe_path}")


def main() -> int:
    try:
        verify_files()
        verify_pyinstaller()
        DIST_DIR.mkdir(parents=True, exist_ok=True)
        SPEC_DIR.mkdir(parents=True, exist_ok=True)
        ENTRYPOINT_DIR.mkdir(parents=True, exist_ok=True)
        compile_checks()

        actions_wrapper = ENTRYPOINT_DIR / "mimir_core_v2_actions_entry.py"
        write_script_wrapper(actions_wrapper, ROOT / "mimir_core_v2_actions.py")

        build_executable("mimir-core-v2-scan", ROOT / "mimir_core_v2_scan.py")
        build_executable("mimir-core-v2-actions", actions_wrapper)

        release_check = ROOT / "mimir_core_v2_release_check.py"
        if release_check.exists():
            release_wrapper = ENTRYPOINT_DIR / "mimir_core_v2_release_check_entry.py"
            write_script_wrapper(release_wrapper, release_check)
            build_executable("mimir-core-v2-release-check", release_wrapper)

        print()
        print("Backend executable build complete")
        print(f"Output folder: {DIST_DIR}")
        print(f"- {DIST_DIR / 'mimir-core-v2-scan.exe'}")
        print(f"- {DIST_DIR / 'mimir-core-v2-actions.exe'}")
        return 0
    except PackagingError as exc:
        print()
        print(f"Backend executable build failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
