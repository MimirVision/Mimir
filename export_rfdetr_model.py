"""Reproduce the reviewed RF-DETR Nano ONNX release artifact.

The output is accepted only when it matches the checksum in model_manifest.json.
Training dependencies are intentionally separate from the packaged runtime.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
MANIFEST_PATH = ROOT / "mimir_core_v2" / "model_manifest.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


VARIANTS = {
    "nano": {"cls": "RFDETRNano", "resolution": 384, "label": "RF-DETR Nano"},
    "small": {"cls": "RFDETRSmall", "resolution": 512, "label": "RF-DETR Small"},
    "medium": {"cls": "RFDETRMedium", "resolution": 576, "label": "RF-DETR Medium"},
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", choices=sorted(VARIANTS), default="nano")
    parser.add_argument("--resolution", type=int, default=0, help="Override the variant's default resolution.")
    parser.add_argument(
        "--update-manifest",
        action="store_true",
        help="Write the new checksum/size into model_manifest.json. Without this the export must match the existing manifest.",
    )
    args = parser.parse_args(argv)

    spec = VARIANTS[args.variant]
    resolution = args.resolution or int(spec["resolution"])
    # RF-DETR resolutions must be divisible by its patch size (16) to tile cleanly.
    if resolution % 16 != 0:
        print(f"Resolution must be divisible by 16: {resolution}", file=sys.stderr)
        return 1

    try:
        import rfdetr
    except ImportError:
        print("Install requirements-training.txt before exporting the model.", file=sys.stderr)
        return 1

    model_cls = getattr(rfdetr, str(spec["cls"]), None)
    if model_cls is None:
        print(f"Installed rfdetr has no {spec['cls']}.", file=sys.stderr)
        return 1

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    record = manifest["models"][0]
    # The artifact filename must name the variant it actually contains, or the
    # bundle silently ships a different model than the path claims.
    relative_name = f"models/rfdetr-{args.variant}.onnx"
    destination = ROOT / (relative_name if args.update_manifest else record["filename"])
    export_dir = ROOT / "training_runs" / "rfdetr_export"
    export_dir.mkdir(parents=True, exist_ok=True)

    model = model_cls(resolution=resolution)
    exported = Path(
        model.export(
            output_dir=str(export_dir),
            shape=(resolution, resolution),
            batch_size=1,
            dynamic_batch=False,
            format="onnx",
            notes=f"Mimir free beta object candidate detector; Apache-designated {spec['label']} weights.",
        )
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(exported, destination)
    actual = sha256(destination)

    if args.update_manifest:
        record["filename"] = relative_name
        record["sha256"] = actual
        record["size_bytes"] = destination.stat().st_size
        record["upstream_model"] = spec["label"]
        record["export_resolution"] = resolution
        manifest["model_files"] = [relative_name]
        manifest["detector_id"] = f"rfdetr_{args.variant}_coco"
        manifest["detector_version"] = f"rfdetr-{args.variant}-onnx-{resolution}"
        manifest["input_size"] = resolution
        MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        print(f"Updated manifest for {spec['label']} @ {resolution}px")
    else:
        expected = str(record["sha256"]).lower()
        if actual != expected:
            print(f"Exported model checksum changed: {actual}", file=sys.stderr)
            print("Re-run with --update-manifest after reviewing, then re-run regression.", file=sys.stderr)
            return 2

    print(f"RF-DETR ONNX ready: {destination}")
    print(f"sha256: {actual}")
    print(f"size: {destination.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
