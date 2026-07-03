import argparse
import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_MODEL = "qwen2.5vl:7b"
YOLO_MODEL_CANDIDATES = [
    "yolo11n.pt",
    "yolov8n.pt",
]


def print_json(payload):
    print(json.dumps(payload, indent=2))


def bundled_runtime_dir():
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", BASE_DIR))

    return BASE_DIR


def prepare_frozen_runtime():
    if getattr(sys, "frozen", False):
        runtime_dir = bundled_runtime_dir()
        os.environ.setdefault("MIMIR_BACKEND_ROOT", str(runtime_dir))
        os.chdir(runtime_dir)


def command_exists(command):
    return shutil.which(command) is not None


def check_output_writable(path):
    path.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(path),
        prefix=".mimir_health_",
        suffix=".tmp",
        delete=False,
    ) as file:
        file.write("ok")
        temp_path = Path(file.name)

    temp_path.unlink(missing_ok=True)


def check_import(module_name):
    importlib.import_module(module_name)


def ollama_models():
    if not command_exists("ollama"):
        return None, "ollama command was not found"

    try:
        result = subprocess.run(
            ["ollama", "list"],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, str(exc)

    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        return None, message or f"ollama list exited with {result.returncode}"

    return result.stdout, None


def health_command(args):
    checks = []
    runtime_dir = bundled_runtime_dir()
    output_dir = runtime_dir / "MimirOutput"

    def add_check(name, required, ok, message=""):
        checks.append(
            {
                "name": name,
                "required": bool(required),
                "ok": bool(ok),
                "message": str(message or ""),
            }
        )

    add_check(
        "backend_cli",
        True,
        True,
        "Backend CLI started.",
    )

    try:
        check_output_writable(output_dir)
        add_check(
            "output_folder_writable",
            True,
            True,
            str(output_dir),
        )
    except Exception as exc:
        add_check(
            "output_folder_writable",
            True,
            False,
            str(exc),
        )

    for module_name, label in [
        ("cv2", "opencv_import"),
        ("ultralytics", "ultralytics_import"),
        ("numpy", "numpy_import"),
        ("requests", "requests_import"),
    ]:
        try:
            check_import(module_name)
            add_check(label, True, True, f"Imported {module_name}.")
        except Exception as exc:
            add_check(label, True, False, str(exc))

    model_files = [
        str(runtime_dir / filename)
        for filename in YOLO_MODEL_CANDIDATES
        if (runtime_dir / filename).exists()
    ]
    add_check(
        "yolo_model_file",
        True,
        bool(model_files),
        ", ".join(model_files) if model_files else "No YOLO .pt model file found.",
    )

    ollama_output, ollama_error = ollama_models()
    add_check(
        "ollama_available",
        False,
        ollama_output is not None,
        "ollama list succeeded." if ollama_output is not None else ollama_error,
    )

    model_name = args.vlm or DEFAULT_MODEL
    model_available = (
        ollama_output is not None
        and model_name.lower() in ollama_output.lower()
    )
    add_check(
        "enhanced_ai_model_available",
        False,
        model_available,
        model_name,
    )

    required_ok = all(
        check["ok"]
        for check in checks
        if check["required"]
    )
    enhanced_ai_available = bool(
        ollama_output is not None
        and model_available
    )

    print("Mimir backend health")
    print("====================")

    for check in checks:
        status = "PASS" if check["ok"] else ("WARN" if not check["required"] else "FAIL")
        print(f"{status} {check['name']}: {check['message']}")

    payload = {
        "ok": required_ok,
        "standard_scanner_available": required_ok,
        "enhanced_ai_available": enhanced_ai_available,
        "enhanced_ai_model": model_name if enhanced_ai_available else None,
        "checks": checks,
    }

    print()
    print_json(payload)

    return 0 if required_ok else 1


def run_existing_main(module_name, argv):
    original_argv = sys.argv[:]
    original_cwd = os.getcwd()

    try:
        prepare_frozen_runtime()
        sys.argv = argv
        module = importlib.import_module(module_name)
        return module.main() or 0
    except SystemExit as exc:
        code = exc.code

        if code is None:
            return 0

        if isinstance(code, int):
            return code

        print(code)
        return 1
    finally:
        sys.argv = original_argv
        try:
            os.chdir(original_cwd)
        except OSError:
            pass


def scan_command(args):
    argv = [
        "tesla_ai_sorter.py",
        "--input",
        args.input,
        "--mode",
        args.mode,
    ]

    if args.vlm:
        argv.extend(["--vlm", args.vlm])

    if args.library_root:
        argv.extend(["--library-root", args.library_root])

    if args.source_action:
        argv.extend(["--source-action", args.source_action])

    return run_existing_main("tesla_ai_sorter", argv)


def action_command(args):
    argv = [
        "mimir_clip_actions.py",
        "--session",
        args.session,
        "--incident-id",
        args.incident_id,
    ]

    if args.set_status:
        argv.extend(["--set-status", args.set_status])

    if args.move_to_library:
        argv.append("--move-to-library")

    if args.delete:
        argv.append("--delete")

    if args.library_root:
        argv.extend(["--library-root", args.library_root])

    return run_existing_main("mimir_clip_actions", argv)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="mimir-backend",
        description="Mimir local backend command line interface.",
    )
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )

    health = subparsers.add_parser(
        "health",
        help="Check local backend readiness.",
    )
    health.add_argument(
        "--vlm",
        default=DEFAULT_MODEL,
        help="Optional enhanced AI model name to check.",
    )
    health.set_defaults(func=health_command)

    scan = subparsers.add_parser(
        "scan",
        help="Scan a footage folder.",
    )
    scan.add_argument("--input", required=True)
    scan.add_argument(
        "--mode",
        choices=["fast", "balanced", "quality"],
        default="balanced",
    )
    scan.add_argument("--vlm")
    scan.add_argument("--library-root")
    scan.add_argument(
        "--source-action",
        choices=[
            "analyze_only",
            "copy_all",
            "move_all",
            "copy_review",
            "move_review",
        ],
        default="analyze_only",
    )
    scan.set_defaults(func=scan_command)

    action = subparsers.add_parser(
        "action",
        help="Apply a manual incident action.",
    )
    action.add_argument("--session", required=True)
    action.add_argument("--incident-id", required=True)
    action.add_argument(
        "--set-status",
        choices=["IGNORE", "REVIEW", "IMPORTANT"],
    )
    action.add_argument("--move-to-library", action="store_true")
    action.add_argument("--delete", action="store_true")
    action.add_argument("--library-root")
    action.set_defaults(func=action_command)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
