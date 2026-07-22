"""Reproduce the reviewed RF-DETR Nano ONNX release artifact.

The output is accepted only when it matches the checksum in model_manifest.json.
Training dependencies are intentionally separate from the packaged runtime.
"""

from __future__ import annotations

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


def main() -> int:
    try:
        from rfdetr import RFDETRNano
    except ImportError:
        print("Install requirements-training.txt before exporting the model.", file=sys.stderr)
        return 1

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    record = manifest["models"][0]
    destination = ROOT / record["filename"]
    export_dir = ROOT / "training_runs" / "rfdetr_export"
    export_dir.mkdir(parents=True, exist_ok=True)
    model = RFDETRNano(resolution=384)
    exported = Path(
        model.export(
            output_dir=str(export_dir),
            shape=(384, 384),
            batch_size=1,
            dynamic_batch=False,
            format="onnx",
            notes="Mimir free beta object candidate detector; Apache-designated RF-DETR Nano weights.",
        )
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(exported, destination)
    actual = sha256(destination)
    expected = str(record["sha256"]).lower()
    if actual != expected:
        print(f"Exported model checksum changed: {actual}", file=sys.stderr)
        print("Review and re-run regression before updating the manifest.", file=sys.stderr)
        return 2
    print(f"RF-DETR ONNX ready: {destination}")
    print(f"sha256: {actual}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
