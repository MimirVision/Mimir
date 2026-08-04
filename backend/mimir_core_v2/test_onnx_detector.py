"""Real-footage smoke test for the packaged RF-DETR ONNX detector."""

from __future__ import annotations

import argparse
from pathlib import Path

from .model_manifest import active_detector_manifest
from .onnx_object_detector import detect_samples, reset_runtime, runtime_diagnostics


def run(input_path: Path) -> None:
    import cv2  # type: ignore

    manifest = active_detector_manifest()
    # Variant-agnostic: the packaged detector may be upgraded (nano -> small ->
    # medium) without this smoke test needing an edit, but it must still be an
    # RF-DETR COCO checkpoint rather than some unreviewed model.
    detector_id = str(manifest.get("detector_id") or "")
    assert detector_id.startswith("rfdetr_") and detector_id.endswith("_coco"), detector_id
    assert manifest.get("license") == "Apache-2.0"
    resolved = manifest.get("resolved_model_files")
    assert isinstance(resolved, list) and resolved
    assert all(item.get("exists") and item.get("checksum_matches") for item in resolved if isinstance(item, dict))

    videos = sorted(path for path in input_path.rglob("*") if path.is_file() and path.suffix.lower() == ".mp4")
    assert videos, f"No real MP4 footage found in {input_path}"
    readable = None
    for video in videos:
        capture = cv2.VideoCapture(str(video))
        try:
            frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            if frame_count > 1:
                capture.set(cv2.CAP_PROP_POS_FRAMES, frame_count // 2)
            ok, frame = capture.read()
        finally:
            capture.release()
        if ok and frame is not None:
            readable = (video, frame)
            break
    assert readable is not None, "No readable real frame was found"
    video, frame = readable
    reset_runtime()
    detections, available, configured = detect_samples(
        [{"frame": frame, "camera": "unknown", "time_sec": 0.0, "source_video": str(video)}]
    )
    assert configured, "RF-DETR is not configured"
    assert available, runtime_diagnostics().get("object_detector_last_error")
    for detection in detections:
        assert detection.get("class_name") in {"person", "vehicle"}
        assert detection.get("detector_backend") == "onnxruntime_rf_detr"
        assert len(detection.get("bbox") or []) == 4
    print("ONNX DETECTOR OK")
    print(f"real frame: {video.name}")
    print(f"detections: {len(detections)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    args = parser.parse_args()
    run(Path(args.input))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
