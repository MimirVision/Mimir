"""Build Windows executables for Mimir Core v2 backend entrypoints.

This script is packaging-only. It compiles existing backend entrypoints and
then uses PyInstaller to produce beta executables under dist_backend.
"""

from __future__ import annotations

import json
import importlib.util
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DIST_DIR = ROOT / "dist_backend"
BUILD_DIR = ROOT / "build_backend"
SPEC_DIR = BUILD_DIR / "specs"
WORK_DIR = BUILD_DIR / "work"

REQUIRED_FILES = [
    ROOT / "mimir_core_v2_scan.py",
    ROOT / "mimir_core_v2_ai_enrich.py",
    ROOT / "mimir_core_v2_actions.py",
    ROOT / "mimir_core_v2_dataset.py",
    ROOT / "mimir_core_v2_model_update.py",
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
    ROOT / "mimir_core_v2" / "video_decode.py",
    ROOT / "mimir_core_v2" / "detector_cache.py",
    ROOT / "mimir_core_v2" / "motion_analysis.py",
    ROOT / "mimir_core_v2" / "evidence_extractor.py",
    ROOT / "mimir_core_v2" / "ego_vehicle.py",
    ROOT / "mimir_core_v2" / "key_moment_refiner.py",
    ROOT / "mimir_core_v2" / "model_manifest.py",
    ROOT / "mimir_core_v2" / "model_update.py",
    ROOT / "mimir_core_v2" / "onnx_object_detector.py",
    ROOT / "mimir_core_v2" / "progress.py",
    ROOT / "mimir_core_v2" / "runtime_paths.py",
    ROOT / "mimir_core_v2" / "thumbnailer.py",
    ROOT / "mimir_core_v2" / "ai_reviewer.py",
    ROOT / "mimir_core_v2" / "ai_enrichment.py",
    ROOT / "mimir_core_v2" / "severity_resolver.py",
    ROOT / "mimir_core_v2" / "output_writer.py",
    ROOT / "mimir_core_v2" / "validators.py",
    ROOT / "mimir_core_v2" / "dataset_package.py",
    ROOT / "mimir_core_v2" / "cvat_client.py",
]


class PackagingError(Exception):
    pass


FORBIDDEN_RELEASE_MODULES = ("torch", "torchvision", "ultralytics", "rfdetr")


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


def verify_runtime_environment() -> None:
    """Reject release builds made from the larger training environment."""

    present = [name for name in FORBIDDEN_RELEASE_MODULES if importlib.util.find_spec(name)]
    if present:
        names = ", ".join(present)
        raise PackagingError(
            "Release build environment contains training-only modules: "
            f"{names}. Build with .venv-runtime and requirements-build.txt."
        )


def compile_checks() -> None:
    compile_targets = list(REQUIRED_FILES)
    compile_targets.extend(path for path in OPTIONAL_FILES if path.exists())
    compile_targets.extend(path for path in CORE_MODULES if path.exists())

    for target in compile_targets:
        run([sys.executable, "-m", "py_compile", str(target)], f"compile {target.name}")


def pyinstaller_command(name: str, entrypoint: Path) -> list[str]:
    command = [
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
    manifest_data: dict[str, object] = {}
    if name in {"mimir-core-v2-scan", "mimir-core-v2-release-check"}:
        manifest = ROOT / "mimir_core_v2" / "model_manifest.json"
        if not manifest.exists():
            raise PackagingError(f"Model manifest is missing: {manifest}")
        command[-1:-1] = ["--add-data", f"{manifest};mimir_core_v2"]
        try:
            manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PackagingError(f"Model manifest is invalid: {exc}") from exc

        license_name = str(manifest_data.get("license_file") or "")
        if license_name:
            license_path = ROOT / license_name
            if not license_path.exists():
                raise PackagingError(f"Required model license is missing: {license_path}")
            destination = str(Path(license_name).parent).replace("\\", "/") or "."
            command[-1:-1] = ["--add-data", f"{license_path};{destination}"]

    if name == "mimir-core-v2-scan":
        for model_name in manifest_data.get("model_files", []):
            model_path = ROOT / str(model_name)
            if not model_path.exists():
                raise PackagingError(f"Required release model is missing: {model_path}")
            destination = str(Path(str(model_name)).parent).replace("\\", "/") or "."
            command[-1:-1] = ["--add-data", f"{model_path};{destination}"]
        command[-1:-1] = ["--hidden-import", "onnxruntime", "--collect-binaries", "onnxruntime"]
    return command


def build_executable(name: str, entrypoint: Path) -> None:
    run(pyinstaller_command(name, entrypoint), f"build {name}.exe")
    exe_path = DIST_DIR / f"{name}.exe"
    if not exe_path.exists():
        raise PackagingError(f"Expected executable was not created: {exe_path}")
    verify_analysis_toc(name)
    print(f"created: {exe_path}")


def verify_analysis_toc(name: str) -> None:
    toc_path = WORK_DIR / name / name / "Analysis-00.toc"
    if not toc_path.exists():
        raise PackagingError(f"PyInstaller analysis manifest is missing: {toc_path}")

    toc_text = toc_path.read_text(encoding="utf-8", errors="replace").lower()
    leaked = []
    for module_name in FORBIDDEN_RELEASE_MODULES:
        markers = (f"'{module_name}'", f"'{module_name}.", f"\\{module_name}\\")
        if any(marker in toc_text for marker in markers):
            leaked.append(module_name)
    if leaked:
        raise PackagingError(
            f"{name}.exe includes training-only modules: {', '.join(leaked)}"
        )


def main() -> int:
    try:
        verify_files()
        verify_pyinstaller()
        verify_runtime_environment()
        DIST_DIR.mkdir(parents=True, exist_ok=True)
        SPEC_DIR.mkdir(parents=True, exist_ok=True)
        compile_checks()

        build_executable("mimir-core-v2-scan", ROOT / "mimir_core_v2_scan.py")
        build_executable("mimir-core-v2-ai-enrich", ROOT / "mimir_core_v2_ai_enrich.py")
        build_executable("mimir-core-v2-actions", ROOT / "mimir_core_v2_actions.py")
        build_executable("mimir-core-v2-dataset", ROOT / "mimir_core_v2_dataset.py")
        build_executable("mimir-core-v2-model-update", ROOT / "mimir_core_v2_model_update.py")

        release_check = ROOT / "mimir_core_v2_release_check.py"
        if release_check.exists():
            build_executable("mimir-core-v2-release-check", release_check)

        print()
        print("Backend executable build complete")
        print(f"Output folder: {DIST_DIR}")
        print(f"- {DIST_DIR / 'mimir-core-v2-scan.exe'}")
        print(f"- {DIST_DIR / 'mimir-core-v2-ai-enrich.exe'}")
        print(f"- {DIST_DIR / 'mimir-core-v2-actions.exe'}")
        print(f"- {DIST_DIR / 'mimir-core-v2-dataset.exe'}")
        print(f"- {DIST_DIR / 'mimir-core-v2-model-update.exe'}")
        return 0
    except PackagingError as exc:
        print()
        print(f"Backend executable build failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
