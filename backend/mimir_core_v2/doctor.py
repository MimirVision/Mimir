"""Report what Mimir Core can actually see, and exit non-zero when it cannot see enough.

Written for containers. A GPU image that silently falls back to CPU looks
exactly like a working one until the bill arrives -- the detector still runs,
still produces verdicts, and is ten times slower for reasons nothing announces.
This makes that state loud.

    python -m mimir_core_v2.doctor
    python -m mimir_core_v2.doctor --json
    python -m mimir_core_v2.doctor --require-gpu

`--require-gpu` is the one for a cloud worker's startup: it fails the container
rather than letting it join the queue and quietly bill CPU time at GPU prices.

Every check is independent and reports on its own. A failure in one does not
hide the rest, because the useful question on a broken image is usually "how
far did it get", not "what was the first thing to break".
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from typing import Any


def _run(command: list[str], timeout: int = 10) -> str:
    """Best-effort external command. Missing binary is a normal answer, not an error."""

    if not shutil.which(command[0]):
        return ""
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def check_python() -> dict[str, Any]:
    return {
        "ok": sys.version_info >= (3, 10),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
    }


def check_onnxruntime() -> dict[str, Any]:
    """Which providers exist, and which one the detector will actually ask for.

    The chosen provider comes from the detector's own resolution function rather
    than being recomputed here. A doctor that agrees with itself and disagrees
    with the code it is diagnosing is worse than no doctor.
    """

    try:
        import onnxruntime as ort  # type: ignore
    except Exception as error:
        return {"ok": False, "error": f"{type(error).__name__}: {error}"}

    from mimir_core_v2.onnx_object_detector import _preferred_provider

    available = list(ort.get_available_providers())
    chosen = _preferred_provider(available)
    gpu_providers = {"CUDAExecutionProvider", "DmlExecutionProvider", "ROCMExecutionProvider"}

    return {
        "ok": True,
        "version": ort.__version__,
        "available_providers": available,
        "chosen_provider": chosen,
        "gpu": chosen in gpu_providers,
        "override": os.environ.get("MIMIR_ONNX_PROVIDER", "") or None,
    }


def check_gpu() -> dict[str, Any]:
    """What the driver says, independently of what ONNX Runtime believes.

    These disagree more often than you would like: nvidia-smi happily lists a
    card in a container started without --gpus, and onnxruntime-gpu reports
    CUDAExecutionProvider as available while failing to initialise it on a
    driver too old for the CUDA build it was compiled against.
    """

    raw = _run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"])
    if not raw:
        return {"ok": False, "detail": "nvidia-smi not present or reported nothing"}

    devices = []
    for line in raw.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) >= 3:
            devices.append({"name": parts[0], "memory": parts[1], "driver": parts[2]})

    return {"ok": bool(devices), "devices": devices}


def check_model() -> dict[str, Any]:
    """Is the detector's model where the manifest says, and does it match?"""

    try:
        from mimir_core_v2.onnx_object_detector import _configuration  # type: ignore

        manifest, path = _configuration()
        return {
            "ok": bool(path and path.is_file()),
            "model": str(path) if path else "",
            "exists": bool(path and path.is_file()),
            "size_bytes": path.stat().st_size if path and path.is_file() else 0,
            "manifest_id": str((manifest or {}).get("id") or (manifest or {}).get("name") or ""),
        }
    except Exception as error:
        return {"ok": False, "error": f"{type(error).__name__}: {error}"}


def check_session() -> dict[str, Any]:
    """Actually load the model. The only check here that proves inference can start.

    Everything above can pass on an image where the model refuses to load --
    wrong opset, truncated download, a CUDA build that needs a newer driver.
    This is the one that would have caught it.
    """

    try:
        from mimir_core_v2.onnx_object_detector import _configuration, _load_session  # type: ignore

        _, path = _configuration()
        if not path or not path.is_file():
            return {"ok": False, "error": "model file not found; see the model check"}

        session = _load_session(path)
        if session is None:
            from mimir_core_v2.onnx_object_detector import _LAST_ERROR  # type: ignore

            return {"ok": False, "error": _LAST_ERROR or "session did not load"}

        return {"ok": True, "providers_in_use": list(session.get_providers())}
    except Exception as error:
        return {"ok": False, "error": f"{type(error).__name__}: {error}"}


def check_video() -> dict[str, Any]:
    """OpenCV present and able to decode. Catches the headless-wheel mistake.

    opencv-python links against GUI libraries a slim container lacks, and the
    failure is an ImportError at first decode rather than at install time --
    long after the image looked fine.
    """

    try:
        import cv2  # type: ignore
    except Exception as error:
        return {"ok": False, "error": f"{type(error).__name__}: {error}"}

    build = cv2.getBuildInformation()
    return {
        "ok": True,
        "opencv": cv2.__version__,
        "ffmpeg": "FFMPEG:                      YES" in build,
    }


CHECKS = {
    "python": check_python,
    "onnxruntime": check_onnxruntime,
    "gpu": check_gpu,
    "model": check_model,
    "session": check_session,
    "video": check_video,
}


def run_all() -> dict[str, Any]:
    report: dict[str, Any] = {"checks": {}}
    for name, check in CHECKS.items():
        try:
            report["checks"][name] = check()
        except Exception as error:
            report["checks"][name] = {"ok": False, "error": f"{type(error).__name__}: {error}"}
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true", help="Machine-readable output.")
    parser.add_argument(
        "--require-gpu",
        action="store_true",
        help="Exit non-zero when the detector would run on CPU. For cloud worker startup.",
    )
    parser.add_argument(
        "--allow-missing-model",
        action="store_true",
        help=(
            "Report the model and session checks without failing on them. For CI, where the "
            "weights are deliberately not in the repository. Never for a container."
        ),
    )
    args = parser.parse_args()

    report = run_all()
    checks = report["checks"]

    # The GPU check is advisory on its own -- a Windows box has no nvidia-smi and
    # is perfectly healthy. What matters is whether the detector got a GPU
    # provider, which is the onnxruntime check.
    essential = ["python", "onnxruntime", "model", "session", "video"]
    if args.allow_missing_model:
        # The weights are a .onnx, which the repository forbids tracking, so a
        # CI checkout has no model and never will. Running here is still worth
        # it -- it catches an import error in this module, which in a container
        # is an image that will not boot -- but demanding a model that cannot
        # be present asserts a container's requirements somewhere that is not a
        # container.
        essential = [name for name in essential if name not in ("model", "session")]
    report["ok"] = all(checks.get(name, {}).get("ok") for name in essential)
    report["model_required"] = not args.allow_missing_model

    on_gpu = bool(checks.get("onnxruntime", {}).get("gpu"))
    report["gpu_active"] = on_gpu
    if args.require_gpu and not on_gpu:
        report["ok"] = False
        report["error"] = "GPU required but the detector resolved to CPU"

    if args.json:
        print(json.dumps(report, indent=2))
        return 0 if report["ok"] else 1

    print("Mimir Core diagnostic")
    print("=" * 21)
    for name in CHECKS:
        result = checks.get(name, {})
        mark = "PASS" if result.get("ok") else ("INFO" if name == "gpu" else "FAIL")
        detail = {key: value for key, value in result.items() if key != "ok"}
        print(f"[{mark}] {name}")
        for key, value in detail.items():
            print(f"         {key}: {value}")

    print()
    if on_gpu:
        print(f"Detector will use {checks['onnxruntime']['chosen_provider']}.")
    else:
        print("Detector will run on CPU. Correct for a CPU box; expensive for a GPU one.")
    print("OVERALL: " + ("PASS" if report["ok"] else "FAIL"))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
