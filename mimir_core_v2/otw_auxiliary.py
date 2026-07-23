"""Shadow door-articulation pretraining from the CC BY 4.0 OTW dataset.

The resulting model recognizes fixed-camera side-door activity versus other
vehicle access and unrelated activity. It is auxiliary context only: it does
not infer physical contact and is never loaded by the production scanner.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "mimir_otw_auxiliary_manifest_v1"
CACHE_VERSION = "mimir_otw_temporal_crops_v1"
MODEL_VERSION = "mimir_otw_door_articulation_v1"
CLASS_NAMES = ("side_door_activity", "other_vehicle_access", "other_activity")
CLASS_INDEX = {name: index for index, name in enumerate(CLASS_NAMES)}
FRAME_SAMPLES = 4
IMAGE_SIZE = 96
INPUT_CHANNELS = FRAME_SAMPLES * 3 + FRAME_SAMPLES - 1


class OtwTrainingError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def sample_frame_indices(start_frame: int, end_frame: int, count: int = FRAME_SAMPLES) -> list[int]:
    start = max(0, int(start_frame))
    end = max(start, int(end_frame))
    if count <= 1 or end == start:
        return [start]
    return [
        int(round(start + (end - start) * index / (count - 1)))
        for index in range(count)
    ]


def interpolate_box(track: list[list[int]], frame_index: int) -> tuple[float, float, float, float] | None:
    points = sorted(
        (point for point in track if isinstance(point, list) and len(point) >= 5),
        key=lambda point: int(point[0]),
    )
    if not points:
        return None
    if frame_index <= int(points[0][0]):
        return tuple(float(value) for value in points[0][1:5])
    if frame_index >= int(points[-1][0]):
        return tuple(float(value) for value in points[-1][1:5])
    for left, right in zip(points, points[1:]):
        left_frame = int(left[0])
        right_frame = int(right[0])
        if left_frame <= frame_index <= right_frame:
            span = max(1, right_frame - left_frame)
            ratio = (frame_index - left_frame) / span
            return tuple(
                float(left[index]) + ratio * (float(right[index]) - float(left[index]))
                for index in range(1, 5)
            )
    return None


def expanded_square_crop(
    frame: Any,
    box: tuple[float, float, float, float],
    image_size: int = IMAGE_SIZE,
) -> Any | None:
    try:
        import cv2  # type: ignore
    except Exception as exc:
        raise OtwTrainingError(f"OpenCV is required for OTW preprocessing: {exc}") from exc
    height, width = frame.shape[:2]
    x1, y1, x2, y2 = box
    center_x = (x1 + x2) / 2.0
    center_y = (y1 + y2) / 2.0
    side = max(x2 - x1, y2 - y1, 32.0) * 1.7
    crop_x1 = max(0, int(math.floor(center_x - side / 2)))
    crop_y1 = max(0, int(math.floor(center_y - side / 2)))
    crop_x2 = min(width, int(math.ceil(center_x + side / 2)))
    crop_y2 = min(height, int(math.ceil(center_y + side / 2)))
    if crop_x2 - crop_x1 < 4 or crop_y2 - crop_y1 < 4:
        return None
    crop = frame[crop_y1:crop_y2, crop_x1:crop_x2]
    resized = cv2.resize(crop, (image_size, image_size), interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)


def temporal_tensor(frames: list[Any]) -> Any:
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except Exception as exc:
        raise OtwTrainingError(f"OpenCV and NumPy are required for OTW preprocessing: {exc}") from exc
    if len(frames) != FRAME_SAMPLES:
        raise OtwTrainingError(f"Expected {FRAME_SAMPLES} temporal crops, received {len(frames)}")
    rgb_channels = [frame.transpose(2, 0, 1) for frame in frames]
    gray = [cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY) for frame in frames]
    differences = [cv2.absdiff(left, right)[None, :, :] for left, right in zip(gray, gray[1:])]
    return np.concatenate([*rgb_channels, *differences], axis=0).astype(np.uint8)


def _read_requested_frames(video_path: Path, requested: set[int]) -> dict[int, Any]:
    try:
        import cv2  # type: ignore
    except Exception as exc:
        raise OtwTrainingError(f"OpenCV is required for OTW preprocessing: {exc}") from exc
    capture = cv2.VideoCapture(str(video_path))
    frames: dict[int, Any] = {}
    try:
        wanted = {max(0, int(frame_index)) for frame_index in requested}
        if not capture.isOpened() or not wanted:
            return frames
        final_index = max(wanted)
        for frame_index in range(final_index + 1):
            if not capture.grab():
                break
            if frame_index not in wanted:
                continue
            readable, frame = capture.retrieve()
            if readable and frame is not None:
                frames[frame_index] = frame
            if len(frames) == len(wanted):
                break
    finally:
        capture.release()
    return frames


def build_cache(
    manifest_path: Path,
    output_path: Path,
    max_samples: int = 0,
) -> dict[str, Any]:
    try:
        import numpy as np  # type: ignore
    except Exception as exc:
        raise OtwTrainingError(f"NumPy is required for OTW preprocessing: {exc}") from exc
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise OtwTrainingError(f"Unsupported OTW manifest: {manifest_path}")
    if manifest.get("promotion_eligible") is not False:
        raise OtwTrainingError("OTW auxiliary promotion guard is missing")
    video_paths = {
        (str(item["collection"]), str(item["video_id"])): Path(str(item.get("local_path") or ""))
        for item in manifest.get("videos", [])
    }
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    activities = list(manifest.get("activities") or [])
    activities.sort(
        key=lambda item: hashlib.sha256(
            (
                f"{item.get('collection')}/{item.get('video_id')}/"
                f"{item.get('activity_id')}/{item.get('activity_label')}"
            ).encode("utf-8")
        ).hexdigest()
    )
    if max_samples > 0:
        by_class: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in activities:
            by_class[str(item.get("target_class") or "")].append(item)
        per_class = max(1, max_samples // max(len(CLASS_NAMES), 1))
        activities = [
            item
            for class_name in CLASS_NAMES
            for item in by_class.get(class_name, [])[:per_class]
        ]
    for item in activities:
        key = (str(item.get("collection") or ""), str(item.get("video_id") or ""))
        grouped[key].append(item)

    samples: list[Any] = []
    labels: list[int] = []
    splits: list[int] = []
    sample_ids: list[str] = []
    skipped: list[dict[str, str]] = []
    split_index = {"train_auxiliary": 0, "validation_auxiliary": 1}
    started = time.perf_counter()
    for video_number, (key, items) in enumerate(sorted(grouped.items()), start=1):
        video_path = video_paths.get(key, Path())
        if not video_path.is_file():
            for item in items:
                skipped.append(
                    {
                        "sample": f"{key[0]}/{key[1]}/{item.get('activity_id')}",
                        "reason": "video_missing",
                    }
                )
            continue
        requests: dict[int, list[tuple[dict[str, Any], int]]] = defaultdict(list)
        item_frames: dict[int, list[int]] = {}
        for item_index, item in enumerate(items):
            indices = sample_frame_indices(item["start_frame"], item["end_frame"])
            item_frames[item_index] = indices
            for frame_index in indices:
                requests[frame_index].append((item, item_index))
        decoded = _read_requested_frames(video_path, set(requests))
        for item_index, item in enumerate(items):
            crops: list[Any] = []
            for frame_index in item_frames[item_index]:
                frame = decoded.get(frame_index)
                box = interpolate_box(item.get("track") or [], frame_index)
                if frame is None or box is None:
                    crops = []
                    break
                crop = expanded_square_crop(frame, box)
                if crop is None:
                    crops = []
                    break
                crops.append(crop)
            if len(crops) != FRAME_SAMPLES:
                skipped.append(
                    {
                        "sample": f"{key[0]}/{key[1]}/{item.get('activity_id')}",
                        "reason": "unreadable_temporal_crop",
                    }
                )
                continue
            class_name = str(item.get("target_class") or "")
            split_name = str(item.get("split") or "")
            if class_name not in CLASS_INDEX or split_name not in split_index:
                continue
            samples.append(temporal_tensor(crops))
            labels.append(CLASS_INDEX[class_name])
            splits.append(split_index[split_name])
            sample_ids.append(
                f"{key[0]}/{key[1]}/{item.get('activity_id')}/{item.get('activity_label')}"
            )
        if video_number == 1 or video_number % 25 == 0 or video_number == len(grouped):
            print(
                f"OTW cache: videos {video_number}/{len(grouped)}, "
                f"samples {len(samples)}, skipped {len(skipped)}"
            )
    if not samples:
        raise OtwTrainingError("OTW preprocessing produced no readable samples")
    matrix = np.stack(samples)
    target = np.asarray(labels, dtype=np.int64)
    split_array = np.asarray(splits, dtype=np.uint8)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        output_path,
        samples=matrix,
        targets=target,
        splits=split_array,
        sample_ids=np.asarray(sample_ids),
    )
    report = {
        "schema_version": CACHE_VERSION,
        "created_at": utc_now(),
        "source_manifest": str(manifest_path),
        "source_manifest_sha256": sha256_file(manifest_path),
        "cache_path": str(output_path),
        "cache_sha256": sha256_file(output_path),
        "sample_count": int(len(matrix)),
        "train_count": int((split_array == 0).sum()),
        "validation_count": int((split_array == 1).sum()),
        "class_counts": {
            name: int((target == index).sum())
            for name, index in CLASS_INDEX.items()
        },
        "shape": list(matrix.shape),
        "input_channels": INPUT_CHANNELS,
        "image_size": IMAGE_SIZE,
        "skipped_count": len(skipped),
        "skipped": skipped[:500],
        "runtime_sec": round(time.perf_counter() - started, 3),
        "promotion_eligible": False,
        "contact_ground_truth_available": False,
    }
    write_json(output_path.with_suffix(".manifest.json"), report)
    return report


def _metrics(targets: Any, predictions: Any) -> dict[str, Any]:
    confusion = [[0 for _ in CLASS_NAMES] for _ in CLASS_NAMES]
    for target, prediction in zip(targets.tolist(), predictions.tolist()):
        confusion[int(target)][int(prediction)] += 1
    per_class: dict[str, dict[str, float | int]] = {}
    for index, name in enumerate(CLASS_NAMES):
        true_positive = confusion[index][index]
        false_positive = sum(confusion[row][index] for row in range(len(CLASS_NAMES)) if row != index)
        false_negative = sum(confusion[index][column] for column in range(len(CLASS_NAMES)) if column != index)
        precision = true_positive / max(true_positive + false_positive, 1)
        recall = true_positive / max(true_positive + false_negative, 1)
        per_class[name] = {
            "support": sum(confusion[index]),
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(2 * precision * recall / max(precision + recall, 1e-12), 4),
        }
    accuracy = sum(confusion[index][index] for index in range(len(CLASS_NAMES))) / max(len(targets), 1)
    return {
        "count": int(len(targets)),
        "accuracy": round(accuracy, 4),
        "macro_f1": round(
            sum(float(item["f1"]) for item in per_class.values()) / len(CLASS_NAMES),
            4,
        ),
        "confusion_matrix": {
            "labels": list(CLASS_NAMES),
            "rows_actual_columns_predicted": confusion,
        },
        "per_class": per_class,
    }


def train(
    manifest_path: Path,
    cache_path: Path,
    output_dir: Path,
    epochs: int = 18,
    batch_size: int = 48,
    learning_rate: float = 0.001,
    seed: int = 20260723,
) -> dict[str, Any]:
    try:
        import numpy as np  # type: ignore
        import torch  # type: ignore
        from torch import nn  # type: ignore
        from torch.utils.data import DataLoader, Dataset  # type: ignore
    except Exception as exc:
        raise OtwTrainingError(f"PyTorch and NumPy are required for OTW training: {exc}") from exc

    class TemporalCropDataset(Dataset):
        def __init__(self, samples: Any, targets: Any, augment: bool):
            self.samples = samples
            self.targets = targets
            self.augment = augment

        def __len__(self) -> int:
            return int(len(self.targets))

        def __getitem__(self, index: int) -> tuple[Any, Any]:
            value = self.samples[index]
            if self.augment and random.random() < 0.5:
                value = value[:, :, ::-1].copy()
            tensor = torch.from_numpy(value.copy()).float().div_(127.5).sub_(1.0)
            return tensor, torch.tensor(int(self.targets[index]), dtype=torch.long)

    class DoorActivityNet(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.features = nn.Sequential(
                nn.Conv2d(INPUT_CHANNELS, 32, kernel_size=5, stride=2, padding=2, bias=False),
                nn.BatchNorm2d(32),
                nn.SiLU(),
                nn.Conv2d(32, 64, kernel_size=3, stride=2, padding=1, bias=False),
                nn.BatchNorm2d(64),
                nn.SiLU(),
                nn.Conv2d(64, 96, kernel_size=3, stride=2, padding=1, bias=False),
                nn.BatchNorm2d(96),
                nn.SiLU(),
                nn.Conv2d(96, 128, kernel_size=3, stride=2, padding=1, bias=False),
                nn.BatchNorm2d(128),
                nn.SiLU(),
                nn.AdaptiveAvgPool2d(1),
            )
            self.classifier = nn.Sequential(
                nn.Flatten(),
                nn.Dropout(0.2),
                nn.Linear(128, len(CLASS_NAMES)),
            )

        def forward(self, value: Any) -> Any:
            return self.classifier(self.features(value))

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise OtwTrainingError(f"Unsupported OTW manifest: {manifest_path}")
    if manifest.get("promotion_eligible") is not False:
        raise OtwTrainingError("OTW auxiliary promotion guard is missing")
    cache_manifest_path = cache_path.with_suffix(".manifest.json")
    cache_manifest = json.loads(cache_manifest_path.read_text(encoding="utf-8"))
    if cache_manifest.get("source_manifest_sha256") != sha256_file(manifest_path):
        raise OtwTrainingError("OTW cache does not match the selected source manifest")
    payload = np.load(cache_path, allow_pickle=False)
    samples = payload["samples"]
    targets = payload["targets"]
    splits = payload["splits"]
    train_mask = splits == 0
    validation_mask = splits == 1
    if not train_mask.any() or not validation_mask.any():
        raise OtwTrainingError("OTW training requires source-isolated train and validation samples")
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    train_dataset = TemporalCropDataset(samples[train_mask], targets[train_mask], augment=True)
    validation_dataset = TemporalCropDataset(
        samples[validation_mask],
        targets[validation_mask],
        augment=False,
    )
    train_loader = DataLoader(
        train_dataset,
        batch_size=max(1, batch_size),
        shuffle=True,
        num_workers=0,
        pin_memory=device.type == "cuda",
    )
    validation_loader = DataLoader(
        validation_dataset,
        batch_size=max(1, batch_size),
        shuffle=False,
        num_workers=0,
        pin_memory=device.type == "cuda",
    )
    train_counts = Counter(int(value) for value in targets[train_mask].tolist())
    class_weights = torch.tensor(
        [
            len(train_dataset) / max(len(CLASS_NAMES) * train_counts[index], 1)
            for index in range(len(CLASS_NAMES))
        ],
        dtype=torch.float32,
        device=device,
    )
    model = DoorActivityNet().to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate, weight_decay=0.01)
    criterion = nn.CrossEntropyLoss(weight=class_weights, label_smoothing=0.04)
    output_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    history: list[dict[str, float | int]] = []
    best_state: dict[str, Any] | None = None
    best_loss = float("inf")
    best_macro_f1 = -1.0
    best_epoch = 0
    stale_epochs = 0
    for epoch in range(1, max(1, epochs) + 1):
        model.train()
        training_loss = 0.0
        training_items = 0
        for values, labels in train_loader:
            values = values.to(device, non_blocking=True)
            labels = labels.to(device, non_blocking=True)
            optimizer.zero_grad(set_to_none=True)
            logits = model(values)
            loss = criterion(logits, labels)
            loss.backward()
            optimizer.step()
            training_loss += float(loss.detach()) * len(labels)
            training_items += len(labels)
        model.eval()
        validation_loss = 0.0
        validation_items = 0
        predictions: list[Any] = []
        actual: list[Any] = []
        with torch.no_grad():
            for values, labels in validation_loader:
                values = values.to(device, non_blocking=True)
                labels = labels.to(device, non_blocking=True)
                logits = model(values)
                loss = criterion(logits, labels)
                validation_loss += float(loss.detach()) * len(labels)
                validation_items += len(labels)
                predictions.append(logits.argmax(dim=1).cpu())
                actual.append(labels.cpu())
        mean_training = training_loss / max(training_items, 1)
        mean_validation = validation_loss / max(validation_items, 1)
        epoch_predictions = torch.cat(predictions).numpy()
        epoch_actual = torch.cat(actual).numpy()
        epoch_metrics = _metrics(epoch_actual, epoch_predictions)
        history.append(
            {
                "epoch": epoch,
                "training_loss": round(mean_training, 6),
                "validation_loss": round(mean_validation, 6),
                "validation_accuracy": epoch_metrics["accuracy"],
                "validation_macro_f1": epoch_metrics["macro_f1"],
            }
        )
        print(
            f"OTW epoch {epoch}/{epochs}: train={mean_training:.4f} "
            f"validation={mean_validation:.4f} macro_f1={epoch_metrics['macro_f1']:.4f}"
        )
        macro_f1 = float(epoch_metrics["macro_f1"])
        better_score = macro_f1 > best_macro_f1 + 1e-5
        tied_score_better_loss = (
            abs(macro_f1 - best_macro_f1) <= 1e-5
            and mean_validation < best_loss - 1e-5
        )
        if better_score or tied_score_better_loss:
            best_loss = mean_validation
            best_macro_f1 = macro_f1
            best_epoch = epoch
            best_state = {name: value.detach().cpu().clone() for name, value in model.state_dict().items()}
            stale_epochs = 0
        else:
            stale_epochs += 1
            if stale_epochs >= 5 and epoch >= 8:
                break
    if best_state is None:
        raise OtwTrainingError("OTW training did not produce a candidate checkpoint")
    model.load_state_dict(best_state)
    model.to(device)
    model.eval()
    final_predictions: list[Any] = []
    final_actual: list[Any] = []
    final_probabilities: list[Any] = []
    with torch.no_grad():
        for values, labels in validation_loader:
            logits = model(values.to(device, non_blocking=True))
            final_probabilities.append(torch.softmax(logits, dim=1).cpu())
            final_predictions.append(logits.argmax(dim=1).cpu())
            final_actual.append(labels)
    validation_predictions = torch.cat(final_predictions).numpy()
    validation_actual = torch.cat(final_actual).numpy()
    probabilities = torch.cat(final_probabilities).numpy()
    validation_metrics = _metrics(validation_actual, validation_predictions)

    checkpoint_path = output_dir / "otw_door_articulation_shadow.pt"
    torch.save(
        {
            "model_state": best_state,
            "class_names": CLASS_NAMES,
            "input_channels": INPUT_CHANNELS,
            "image_size": IMAGE_SIZE,
            "model_version": MODEL_VERSION,
        },
        checkpoint_path,
    )
    onnx_path = output_dir / "otw_door_articulation_shadow.onnx"
    model_cpu = model.cpu()
    dummy = torch.zeros(1, INPUT_CHANNELS, IMAGE_SIZE, IMAGE_SIZE, dtype=torch.float32)
    torch.onnx.export(
        model_cpu,
        dummy,
        str(onnx_path),
        input_names=["temporal_crops"],
        output_names=["activity_logits"],
        dynamic_axes={"temporal_crops": {0: "batch"}, "activity_logits": {0: "batch"}},
        opset_version=17,
        dynamo=False,
    )
    try:
        import onnx  # type: ignore

        onnx.checker.check_model(onnx.load(str(onnx_path)))
    except Exception as exc:
        raise OtwTrainingError(f"OTW ONNX validation failed: {exc}") from exc
    try:
        import onnxruntime as ort  # type: ignore

        parity_input = (
            samples[validation_mask][: min(8, int(validation_mask.sum()))]
            .astype("float32")
            / 127.5
            - 1.0
        )
        with torch.no_grad():
            torch_logits = model_cpu(torch.from_numpy(parity_input)).numpy()
        session = ort.InferenceSession(
            str(onnx_path),
            providers=["CPUExecutionProvider"],
        )
        onnx_logits = session.run(
            ["activity_logits"],
            {"temporal_crops": parity_input},
        )[0]
        onnx_max_abs_error = float(np.max(np.abs(torch_logits - onnx_logits)))
        if not np.isfinite(onnx_max_abs_error) or onnx_max_abs_error > 1e-4:
            raise OtwTrainingError(
                f"OTW ONNX parity failed: max absolute error {onnx_max_abs_error:.8f}"
            )
    except OtwTrainingError:
        raise
    except Exception as exc:
        raise OtwTrainingError(f"OTW ONNX Runtime smoke test failed: {exc}") from exc

    validation_rows = []
    validation_ids = payload["sample_ids"][validation_mask].tolist()
    for sample_id, target, prediction, probability in zip(
        validation_ids,
        validation_actual.tolist(),
        validation_predictions.tolist(),
        probabilities.tolist(),
    ):
        validation_rows.append(
            {
                "sample_id": str(sample_id),
                "actual": CLASS_NAMES[int(target)],
                "predicted": CLASS_NAMES[int(prediction)],
                "probabilities": {
                    name: round(float(probability[index]), 6)
                    for index, name in enumerate(CLASS_NAMES)
                },
            }
        )
    predictions_path = output_dir / "otw_validation_predictions.json"
    write_json(
        predictions_path,
        {
            "schema_version": "mimir_otw_validation_predictions_v1",
            "model_version": MODEL_VERSION,
            "rows": validation_rows,
        },
    )
    result = {
        "schema_version": "mimir_auxiliary_model_manifest_v1",
        "model_version": MODEL_VERSION,
        "created_at": utc_now(),
        "purpose": "stationary side-door articulation and hard-negative pretraining",
        "production_detector": False,
        "contact_detector": False,
        "promotion_eligible": False,
        "physical_contact_claim_allowed": False,
        "contact_ground_truth_available": False,
        "source_id": manifest.get("source_id"),
        "source_manifest": str(manifest_path),
        "source_manifest_sha256": sha256_file(manifest_path),
        "source_license": manifest.get("license"),
        "source_license_url": manifest.get("license_url"),
        "source_attribution": manifest.get("attribution"),
        "cache_path": str(cache_path),
        "cache_sha256": sha256_file(cache_path),
        "checkpoint_path": str(checkpoint_path),
        "checkpoint_sha256": sha256_file(checkpoint_path),
        "model_path": str(onnx_path),
        "model_sha256": sha256_file(onnx_path),
        "onnx_runtime_parity_max_abs_error": round(onnx_max_abs_error, 10),
        "validation_predictions": str(predictions_path),
        "validation_predictions_sha256": sha256_file(predictions_path),
        "class_names": list(CLASS_NAMES),
        "input_channels": INPUT_CHANNELS,
        "image_size": IMAGE_SIZE,
        "frame_samples": FRAME_SAMPLES,
        "train_samples": int(train_mask.sum()),
        "validation_samples": int(validation_mask.sum()),
        "device": str(device),
        "epochs_requested": int(epochs),
        "best_epoch": best_epoch,
        "checkpoint_selection": "validation_macro_f1_then_loss",
        "best_validation_loss": round(best_loss, 6),
        "history": history,
        "validation_metrics": validation_metrics,
        "runtime_sec": round(time.perf_counter() - started, 3),
        "limitations": [
            "OTW is fixed security-camera footage, not Tesla Sentry camera footage.",
            "Door activity labels do not establish contact with another vehicle.",
            "This model may provide auxiliary initialization only after target-domain evaluation.",
        ],
    }
    checksum_payload = json.dumps(result, sort_keys=True, separators=(",", ":")).encode("utf-8")
    result["manifest_checksum"] = hashlib.sha256(checksum_payload).hexdigest()
    write_json(output_dir / "otw_auxiliary_model_manifest.json", result)
    return result
