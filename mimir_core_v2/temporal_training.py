"""Candidate-only temporal contact feature extraction and ONNX training.

This module is deliberately isolated from the production detector. A trained
artifact remains unpromoted until locked evaluation passes.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import random
import statistics
import uuid
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


FEATURE_VERSION = "mimir_temporal_features_v1"
TRAINING_SEED = 20260722
TEMPORAL_ARCHITECTURE = "dilated_conv_v3_alert_window_6s"
FEATURE_NAMES = (
    "global_frame_difference",
    "localized_frame_difference",
    "optical_flow_mean",
    "optical_flow_p95",
    "camera_translation",
    "motion_impulse",
    "ego_foreign_intersection",
    "ego_foreign_distance",
    "approach_velocity",
    "door_articulation",
    "nearby_persistence",
    "post_contact_separation",
)


class TemporalTrainingError(RuntimeError):
    """Raised when candidate feature extraction or training cannot proceed."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TemporalTrainingError(f"Expected a JSON object: {path}")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _boxes_at_time(objects: list[dict[str, Any]], time_sec: float, tolerance: float) -> list[dict[str, Any]]:
    candidates = []
    for item in objects:
        if not isinstance(item, dict):
            continue
        try:
            distance = abs(float(item.get("time_sec")) - time_sec)
        except (TypeError, ValueError):
            continue
        box = item.get("bbox_xyxy")
        if distance <= tolerance and isinstance(box, list) and len(box) == 4:
            candidates.append({**item, "_time_distance": distance})
    nearest: dict[tuple[str, str], dict[str, Any]] = {}
    for item in candidates:
        key = (str(item.get("class_name") or ""), str(item.get("track_id") or "default"))
        if key not in nearest or item["_time_distance"] < nearest[key]["_time_distance"]:
            nearest[key] = item
    return list(nearest.values())


def _box_area(box: list[float]) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _box_intersection(a: list[float], b: list[float]) -> float:
    width = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    height = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    return width * height


def _box_distance(a: list[float], b: list[float], width: float, height: float) -> float:
    dx = max(a[0] - b[2], b[0] - a[2], 0.0)
    dy = max(a[1] - b[3], b[1] - a[3], 0.0)
    diagonal = max(1.0, math.hypot(width, height))
    return min(1.0, math.hypot(dx, dy) / diagonal)


def _mask_geometry(
    ego_item: dict[str, Any],
    foreign_item: dict[str, Any],
    frame_width: int,
    frame_height: int,
) -> tuple[float, float] | None:
    """Return apparent mask intersection and distance at a compact resolution."""
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except ImportError:
        return None
    ego_polygons = ego_item.get("segmentation")
    foreign_polygons = foreign_item.get("segmentation")
    if not isinstance(ego_polygons, list) or not isinstance(foreign_polygons, list):
        return None
    target_width, target_height = 320, 180

    def rasterize(polygons: list[Any]) -> Any:
        mask = np.zeros((target_height, target_width), dtype=np.uint8)
        contours = []
        for polygon in polygons:
            if not isinstance(polygon, list) or len(polygon) < 6 or len(polygon) % 2:
                continue
            points = np.asarray(polygon, dtype=np.float32).reshape(-1, 2)
            points[:, 0] *= target_width / max(1, frame_width)
            points[:, 1] *= target_height / max(1, frame_height)
            contours.append(np.rint(points).astype(np.int32))
        if contours:
            cv2.fillPoly(mask, contours, 1)
        return mask

    ego_mask = rasterize(ego_polygons)
    foreign_mask = rasterize(foreign_polygons)
    ego_area = int(ego_mask.sum())
    foreign_area = int(foreign_mask.sum())
    if not ego_area or not foreign_area:
        return None
    overlap = int((ego_mask & foreign_mask).sum())
    intersection = min(1.0, overlap / max(1, min(ego_area, foreign_area)))
    if overlap:
        return intersection, 0.0
    distance_map = cv2.distanceTransform((1 - ego_mask).astype(np.uint8), cv2.DIST_L2, 3)
    distance_pixels = float(distance_map[foreign_mask.astype(bool)].min())
    return intersection, min(1.0, distance_pixels / math.hypot(target_width, target_height))


def _annotation_geometry(
    objects: list[dict[str, Any]],
    time_sec: float,
    frame_width: int,
    frame_height: int,
    previous_distance: float,
    sample_step_sec: float,
) -> tuple[list[float], float]:
    nearby = _boxes_at_time(objects, time_sec, max(0.12, sample_step_sec * 0.75))
    ego = [item for item in nearby if item.get("class_name") == "ego_vehicle"]
    foreign = [item for item in nearby if item.get("class_name") in {"person", "vehicle", "vehicle_door"}]
    intersection = 0.0
    distance = 1.0
    door_articulation = 0.0
    if ego and foreign:
        ego_box = [float(value) for value in ego[0]["bbox_xyxy"]]
        for item in foreign:
            box = [float(value) for value in item["bbox_xyxy"]]
            mask_geometry = _mask_geometry(ego[0], item, frame_width, frame_height)
            if mask_geometry is not None:
                pair_intersection, pair_distance = mask_geometry
            else:
                pair_intersection = min(
                    1.0,
                    _box_intersection(ego_box, box)
                    / max(1.0, min(_box_area(ego_box), _box_area(box))),
                )
                pair_distance = _box_distance(ego_box, box, frame_width, frame_height)
            intersection = max(intersection, pair_intersection)
            distance = min(distance, pair_distance)
            if item.get("class_name") == "vehicle_door":
                state = str(item.get("door_state") or "")
                door_articulation = max(door_articulation, 1.0 if state in {"opening", "closing"} else 0.5)
    approach = 0.0 if previous_distance >= 1.0 else (previous_distance - distance) / max(sample_step_sec, 1e-3)
    persistence = 1.0 if distance < 0.08 else 0.5 if distance < 0.2 else 0.0
    separation = max(0.0, distance - previous_distance) / max(sample_step_sec, 1e-3)
    return [intersection, distance, approach, door_articulation, persistence, separation], distance


@dataclass
class ExtractedSequence:
    incident_id: str
    split: str
    feature_path: str
    frames: int
    fps: float
    duration_sec: float
    contact_time_sec: float | None
    impact_time_sec: float | None
    event_time_sec: float | None
    event_target: int
    alert_time_sec: float | None
    time_to_accident_sec: float | None
    outcome: str
    source_group_hash: str
    geometry_provenance: str


def extract_sequence(item: dict[str, Any], output: Path, sample_fps: float = 15.0) -> ExtractedSequence:
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except ImportError as exc:
        raise TemporalTrainingError("Install requirements-training.txt before extracting temporal features.") from exc
    cv2.setNumThreads(1)

    media = item.get("media") if isinstance(item.get("media"), list) else []
    media_path = next((Path(str(value)) for value in media if Path(str(value)).is_file()), None)
    if media_path is None:
        raise TemporalTrainingError(f"No readable media for {item.get('incident_id')}")
    incident_id = str(item.get("incident_id") or uuid.uuid4().hex)
    feature_path = output / f"{incident_id}.npz"
    contact_time = item.get("apparent_contact_time_sec")
    impact_time = item.get("impact_time_sec")
    event_time = item.get("external_event_time_sec")
    alert_time = item.get("external_alert_time_sec")
    time_to_accident = item.get("external_time_to_accident_sec")
    event_target = int(item.get("external_event_target") if item.get("external_event_target") is not None else event_time is not None)
    contact_value = float(contact_time) if contact_time is not None else math.nan
    impact_value = float(impact_time) if impact_time is not None else math.nan
    event_value = float(event_time) if event_time is not None else math.nan
    alert_value = float(alert_time) if alert_time is not None else math.nan
    time_to_accident_value = float(time_to_accident) if time_to_accident is not None else math.nan
    if isinstance(item.get("perception_objects"), list):
        objects = item["perception_objects"]
        geometry_provenance = "candidate_segmentation"
    else:
        objects = item.get("objects") if isinstance(item.get("objects"), list) else []
        geometry_provenance = str(
            item.get("geometry_provenance") or ("human_annotation" if objects else "motion_only")
        )

    if feature_path.is_file():
        try:
            with np.load(str(feature_path)) as payload:
                existing_features = payload["features"]
                existing_times = payload["times"]
                if (
                    existing_features.ndim == 2
                    and existing_features.shape[1] == len(FEATURE_NAMES)
                    and len(existing_features) == len(existing_times)
                    and len(existing_times) > 0
                ):
                    inferred_fps = 1.0 / max(1e-6, float(existing_times[1] - existing_times[0])) if len(existing_times) > 1 else sample_fps
                    duration = float(item.get("duration_sec") or float(existing_times[-1]) + 1.0 / inferred_fps)
                    return ExtractedSequence(
                        incident_id=incident_id,
                        split=str(item.get("split") or "train"),
                        feature_path=str(feature_path.resolve()),
                        frames=len(existing_times),
                        fps=inferred_fps,
                        duration_sec=duration,
                        contact_time_sec=None if math.isnan(contact_value) else contact_value,
                        impact_time_sec=None if math.isnan(impact_value) else impact_value,
                        event_time_sec=None if math.isnan(event_value) else event_value,
                        event_target=event_target,
                        alert_time_sec=None if math.isnan(alert_value) else alert_value,
                        time_to_accident_sec=None if math.isnan(time_to_accident_value) else time_to_accident_value,
                        outcome=str(item.get("contact_outcome") or "uncertain"),
                        source_group_hash=str(item.get("source_group_hash") or ""),
                        geometry_provenance=geometry_provenance,
                    )
        except (KeyError, OSError, ValueError):
            feature_path.unlink(missing_ok=True)

    capture = cv2.VideoCapture(str(media_path))
    native_fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration = float(item.get("duration_sec") or 0.0)
    if duration <= 0 and native_fps > 0 and frame_count > 0:
        duration = frame_count / native_fps
    if duration <= 0:
        capture.release()
        raise TemporalTrainingError(f"Video duration is unavailable: {media_path}")
    actual_sample_fps = min(max(1.0, sample_fps), native_fps if native_fps > 0 else sample_fps)
    sample_step = 1.0 / actual_sample_fps
    rows: list[list[float]] = []
    valid_times: list[float] = []
    previous_gray = None
    previous_diff = 0.0
    previous_distance = 1.0
    try:
        frame_index = 0
        next_sample_frame = 0.0
        sample_frame_step = native_fps / actual_sample_fps if native_fps > 0 else 1.0
        while True:
            ok, frame = capture.read()
            if not ok or frame is None:
                break
            if native_fps > 0 and frame_index + 1e-6 < next_sample_frame:
                frame_index += 1
                continue
            time_sec = frame_index / native_fps if native_fps > 0 else float(capture.get(cv2.CAP_PROP_POS_MSEC) or 0.0) / 1000.0
            next_sample_frame += sample_frame_step
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            gray = cv2.resize(gray, (320, 180), interpolation=cv2.INTER_AREA)
            if previous_gray is None:
                global_diff = local_diff = flow_mean = flow_p95 = camera_translation = 0.0
            else:
                difference = cv2.absdiff(gray, previous_gray).astype(np.float32) / 255.0
                global_diff = float(difference.mean())
                cells = [
                    difference[y : y + 45, x : x + 64]
                    for y in range(0, 180, 45)
                    for x in range(0, 320, 64)
                ]
                local_diff = max(float(cell.mean()) for cell in cells if cell.size)
                flow = cv2.calcOpticalFlowFarneback(previous_gray, gray, None, 0.5, 3, 15, 3, 5, 1.2, 0)
                magnitudes = np.sqrt(flow[..., 0] ** 2 + flow[..., 1] ** 2)
                flow_mean = min(1.0, float(magnitudes.mean()) / 12.0)
                flow_p95 = min(1.0, float(np.percentile(magnitudes, 95)) / 24.0)
                shift, _ = cv2.phaseCorrelate(previous_gray.astype(np.float32), gray.astype(np.float32))
                camera_translation = min(1.0, math.hypot(float(shift[0]), float(shift[1])) / 40.0)
            motion_impulse = max(0.0, global_diff - previous_diff)
            geometry, previous_distance = _annotation_geometry(
                objects,
                float(time_sec),
                int(frame.shape[1]),
                int(frame.shape[0]),
                previous_distance,
                sample_step,
            )
            rows.append(
                [global_diff, local_diff, flow_mean, flow_p95, camera_translation, motion_impulse, *geometry]
            )
            valid_times.append(float(time_sec))
            previous_gray = gray
            previous_diff = global_diff
            frame_index += 1
    finally:
        capture.release()
    if not rows:
        raise TemporalTrainingError(f"No frames could be sampled: {media_path}")

    feature_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        feature_path,
        features=np.asarray(rows, dtype=np.float32),
        times=np.asarray(valid_times, dtype=np.float32),
        contact_time_sec=np.asarray([contact_value], dtype=np.float32),
        impact_time_sec=np.asarray([impact_value], dtype=np.float32),
        event_time_sec=np.asarray([event_value], dtype=np.float32),
        event_target=np.asarray([event_target], dtype=np.int8),
        alert_time_sec=np.asarray([alert_value], dtype=np.float32),
        time_to_accident_sec=np.asarray([time_to_accident_value], dtype=np.float32),
    )
    return ExtractedSequence(
        incident_id=incident_id,
        split=str(item.get("split") or "train"),
        feature_path=str(feature_path.resolve()),
        frames=len(rows),
        fps=actual_sample_fps,
        duration_sec=duration,
        contact_time_sec=None if math.isnan(contact_value) else contact_value,
        impact_time_sec=None if math.isnan(impact_value) else impact_value,
        event_time_sec=None if math.isnan(event_value) else event_value,
        event_target=event_target,
        alert_time_sec=None if math.isnan(alert_value) else alert_value,
        time_to_accident_sec=None if math.isnan(time_to_accident_value) else time_to_accident_value,
        outcome=str(item.get("contact_outcome") or "uncertain"),
        source_group_hash=str(item.get("source_group_hash") or ""),
        geometry_provenance=geometry_provenance,
    )


def _extract_sequence_worker(item: dict[str, Any], output: Path, sample_fps: float) -> dict[str, Any]:
    return extract_sequence(item, output, sample_fps).__dict__


def extract_feature_dataset(
    prepared: Path,
    output: Path,
    sample_fps: float = 15.0,
    workers: int = 1,
) -> dict[str, Any]:
    manifest = read_json(prepared / "training_manifest.json")
    items = manifest.get("temporal_items") if isinstance(manifest.get("temporal_items"), list) else []
    if not items:
        raise TemporalTrainingError("Prepared dataset has no consented temporal items.")
    sequences: list[dict[str, Any]] = []
    errors: list[str] = []
    valid_items = [item for item in items if isinstance(item, dict)]
    worker_count = max(1, min(int(workers), os.cpu_count() or 1))
    if worker_count == 1:
        for index, item in enumerate(valid_items, start=1):
            try:
                sequences.append(_extract_sequence_worker(item, output / "sequences", sample_fps))
            except (OSError, ValueError, TemporalTrainingError) as exc:
                errors.append(str(exc))
            if index % 25 == 0 or index == len(valid_items):
                print(f"Temporal features: {index}/{len(valid_items)}")
    else:
        with ProcessPoolExecutor(max_workers=worker_count) as executor:
            pending = {
                executor.submit(_extract_sequence_worker, item, output / "sequences", sample_fps): item
                for item in valid_items
            }
            for index, future in enumerate(as_completed(pending), start=1):
                try:
                    sequences.append(future.result())
                except Exception as exc:
                    errors.append(f"{pending[future].get('incident_id')}: {type(exc).__name__}: {exc}")
                if index % 25 == 0 or index == len(valid_items):
                    print(f"Temporal features: {index}/{len(valid_items)}")
    sequences.sort(key=lambda item: str(item.get("incident_id") or ""))
    result = {
        "schema_version": FEATURE_VERSION,
        "created_at": utc_now(),
        "prepared_manifest": str((prepared / "training_manifest.json").resolve()),
        "prepared_manifest_sha256": sha256(prepared / "training_manifest.json"),
        "sample_fps": sample_fps,
        "workers": worker_count,
        "feature_names": list(FEATURE_NAMES),
        "sequences": sequences,
        "errors": errors,
    }
    write_json(output / "temporal_features_manifest.json", result)
    if not sequences:
        raise TemporalTrainingError("No temporal feature sequences could be extracted.")
    return result


def _pilot_blockers(source_audit: dict[str, Any], min_groups: int, min_positives: int, min_negatives: int) -> list[str]:
    blockers = []
    groups = int(source_audit.get("complete_items") or 0)
    positives = int(source_audit.get("positive_items") or 0)
    negatives = int(source_audit.get("hard_negative_items") or 0)
    relabeled = int(source_audit.get("blind_relabel_items") or 0)
    required_relabels = math.ceil(groups * 0.1)
    split_counts = source_audit.get("complete_split_counts") if isinstance(source_audit.get("complete_split_counts"), dict) else {}
    if groups < min_groups:
        blockers.append(f"{groups} complete groups; pilot minimum is {min_groups}")
    if positives < min_positives:
        blockers.append(f"{positives} positives; pilot minimum is {min_positives}")
    if negatives < min_negatives:
        blockers.append(f"{negatives} hard negatives; pilot minimum is {min_negatives}")
    if relabeled < required_relabels:
        blockers.append(f"{relabeled} blind re-labels; at least {required_relabels} (10%) are required")
    for split_name in ("train", "validation", "test"):
        if int(split_counts.get(split_name) or 0) <= 0:
            blockers.append(f"no complete source-isolated {split_name} groups are available")
    return blockers


def train_temporal_candidate(
    feature_root: Path,
    prepared: Path,
    output: Path,
    epochs: int = 40,
    learning_rate: float = 1e-3,
    min_groups: int = 100,
    min_positives: int = 25,
    min_negatives: int = 25,
    pretrained_checkpoint: Path | None = None,
) -> dict[str, Any]:
    try:
        import numpy as np  # type: ignore
        import torch  # type: ignore
        from torch import nn  # type: ignore
    except ImportError as exc:
        raise TemporalTrainingError("PyTorch, NumPy, and ONNX are required in the training environment.") from exc
    training_manifest = read_json(prepared / "training_manifest.json")
    audit = training_manifest.get("source_audit") if isinstance(training_manifest.get("source_audit"), dict) else {}
    blockers = _pilot_blockers(audit, min_groups, min_positives, min_negatives)
    if blockers:
        raise TemporalTrainingError("TRAINING BLOCKED\n- " + "\n- ".join(blockers))
    feature_manifest = read_json(feature_root / "temporal_features_manifest.json")
    all_sequences = [item for item in feature_manifest.get("sequences", []) if isinstance(item, dict)]
    sequences = [item for item in all_sequences if item.get("split") == "train"]
    validation_sequences = [item for item in all_sequences if item.get("split") == "validation"]
    if not sequences:
        raise TemporalTrainingError("Temporal feature manifest has no train sequences.")
    if not validation_sequences:
        raise TemporalTrainingError("Temporal feature manifest has no source-isolated validation sequences.")

    class TemporalVerifier(nn.Module):
        def __init__(self, features: int):
            super().__init__()
            self.encoder = nn.Sequential(
                nn.Conv1d(features, 64, kernel_size=5, padding=2),
                nn.ReLU(),
                nn.Dropout(0.1),
                nn.Conv1d(64, 64, kernel_size=5, padding=4, dilation=2),
                nn.ReLU(),
                nn.Dropout(0.1),
                nn.Conv1d(64, 64, kernel_size=5, padding=8, dilation=4),
                nn.ReLU(),
                nn.Conv1d(64, 64, kernel_size=5, padding=16, dilation=8),
                nn.ReLU(),
            )
            self.head = nn.Conv1d(64, 2, kernel_size=1)

        def forward(self, values: Any) -> Any:
            encoded = self.encoder(values.transpose(1, 2))
            return self.head(encoded).transpose(1, 2)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    random.seed(TRAINING_SEED)
    np.random.seed(TRAINING_SEED)
    torch.manual_seed(TRAINING_SEED)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(TRAINING_SEED)
    model = TemporalVerifier(len(FEATURE_NAMES)).to(device)
    pretrained_sha256 = None
    if pretrained_checkpoint is not None:
        pretrained_checkpoint = pretrained_checkpoint.resolve()
        if not pretrained_checkpoint.is_file():
            raise TemporalTrainingError(f"Auxiliary pretraining checkpoint does not exist: {pretrained_checkpoint}")
        try:
            payload = torch.load(str(pretrained_checkpoint), map_location=device, weights_only=True)
        except TypeError:
            payload = torch.load(str(pretrained_checkpoint), map_location=device)
        encoder_state = payload.get("encoder_state_dict") if isinstance(payload, dict) else None
        if not isinstance(encoder_state, dict):
            raise TemporalTrainingError("Auxiliary checkpoint has no encoder_state_dict")
        model.encoder.load_state_dict(encoder_state, strict=True)
        pretrained_sha256 = sha256(pretrained_checkpoint)
    optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate, weight_decay=1e-4)
    # Contact/impact targets occupy only a small temporal window. Fixed class
    # weights avoid an all-negative shortcut without tuning production rules.
    loss_function = nn.BCEWithLogitsLoss(
        pos_weight=torch.tensor([8.0, 12.0], dtype=torch.float32, device=device)
    )
    model.train()
    losses: list[float] = []
    validation_losses: list[float] = []
    best_validation_loss = math.inf
    best_epoch = 0
    best_state: dict[str, Any] | None = None

    def calculate_loss(item: dict[str, Any]) -> Any:
        with np.load(str(item["feature_path"])) as payload:
            feature_values = payload["features"].copy()
            times = payload["times"].astype(np.float32)
        features = torch.tensor(feature_values, dtype=torch.float32, device=device).unsqueeze(0)
        targets = np.zeros((len(times), 2), dtype=np.float32)
        contact_time = item.get("contact_time_sec")
        impact_time = item.get("impact_time_sec")
        if contact_time is not None:
            targets[:, 0] = np.exp(-0.5 * ((times - float(contact_time)) / 0.35) ** 2)
        if impact_time is not None:
            targets[:, 1] = np.exp(-0.5 * ((times - float(impact_time)) / 0.25) ** 2)
        target_tensor = torch.tensor(targets, dtype=torch.float32, device=device).unsqueeze(0)
        return loss_function(model(features), target_tensor)

    for epoch in range(max(1, epochs)):
        model.train()
        epoch_losses = []
        epoch_sequences = list(sequences)
        random.Random(TRAINING_SEED + epoch).shuffle(epoch_sequences)
        for item in epoch_sequences:
            optimizer.zero_grad(set_to_none=True)
            loss = calculate_loss(item)
            loss.backward()
            optimizer.step()
            epoch_losses.append(float(loss.detach().cpu()))
        losses.append(statistics.fmean(epoch_losses))
        model.eval()
        with torch.no_grad():
            validation_loss = statistics.fmean(
                float(calculate_loss(item).detach().cpu()) for item in validation_sequences
            )
        validation_losses.append(validation_loss)
        if validation_loss < best_validation_loss:
            best_validation_loss = validation_loss
            best_epoch = epoch + 1
            best_state = {name: value.detach().cpu().clone() for name, value in model.state_dict().items()}

    if best_state is None:
        raise TemporalTrainingError("Temporal validation did not produce a usable checkpoint.")
    model.load_state_dict(best_state)

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    run_dir = output / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    checkpoint = run_dir / "temporal_contact_verifier.pt"
    torch.save({"state_dict": model.state_dict(), "feature_names": FEATURE_NAMES}, checkpoint)
    onnx_path = run_dir / "temporal_contact_verifier.onnx"
    model.eval().cpu()
    example = torch.zeros((1, 32, len(FEATURE_NAMES)), dtype=torch.float32)
    torch.onnx.export(
        model,
        example,
        onnx_path,
        input_names=["features"],
        output_names=["contact_impact_logits"],
        dynamic_axes={"features": {1: "time"}, "contact_impact_logits": {1: "time"}},
        opset_version=17,
        dynamo=False,
    )
    candidate_id = f"mimir-temporal-{run_id}"
    model_manifest = {
        "schema_version": "mimir_candidate_model_manifest_v1",
        "candidate_id": candidate_id,
        "candidate_kind": "temporal_contact_verifier",
        "created_at": utc_now(),
        "model_path": str(onnx_path.resolve()),
        "sha256": sha256(onnx_path),
        "license": (
            "Mimir proprietary beta candidate; consented data with Nexar-licensed auxiliary initialization"
            if pretrained_checkpoint is not None
            else "Mimir proprietary beta candidate; consented training data only"
        ),
        "promoted": False,
        "feature_version": FEATURE_VERSION,
        "temporal_architecture": TEMPORAL_ARCHITECTURE,
        "feature_names": list(FEATURE_NAMES),
        "outputs": ["contact_probability", "impact_probability", "best_frame", "timing_uncertainty_sec"],
        "training_run_id": run_id,
        "auxiliary_pretraining_checkpoint_sha256": pretrained_sha256,
        "decision_policy": {
            "frozen_before_locked_evaluation": True,
            "contact_review_probability": 0.5,
            "impact_important_probability": 0.5,
        },
    }
    run_manifest = {
        "schema_version": "mimir_training_run_v1",
        "run_id": run_id,
        "started_at": utc_now(),
        "completed_at": utc_now(),
        "candidate_kind": "temporal_contact_verifier",
        "dataset_manifest_sha256": sha256(prepared / "training_manifest.json"),
        "feature_manifest_sha256": sha256(feature_root / "temporal_features_manifest.json"),
        "status": "complete",
        "cuda_device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "epochs": epochs,
        "learning_rate": learning_rate,
        "training_seed": TRAINING_SEED,
        "auxiliary_pretraining_checkpoint": str(pretrained_checkpoint) if pretrained_checkpoint else None,
        "auxiliary_pretraining_checkpoint_sha256": pretrained_sha256,
        "loss_history": losses,
        "validation_loss_history": validation_losses,
        "best_validation_loss": best_validation_loss,
        "best_epoch": best_epoch,
        "artifacts": [
            {"path": str(checkpoint), "sha256": sha256(checkpoint)},
            {"path": str(onnx_path), "sha256": sha256(onnx_path)},
        ],
    }
    write_json(run_dir / "candidate_model_manifest.json", model_manifest)
    write_json(run_dir / "run_manifest.json", run_manifest)
    return {"run": run_manifest, "model": model_manifest}


def pretrain_external_event_candidate(
    feature_root: Path,
    prepared: Path,
    output: Path,
    epochs: int = 10,
    learning_rate: float = 1e-3,
) -> dict[str, Any]:
    """Pretrain motion features on Nexar collision-or-near-miss timing labels.

    This artifact is an encoder initializer only. Nexar positives include near
    misses, so this function never labels them as physical contact or impact.
    """
    try:
        import numpy as np  # type: ignore
        import torch  # type: ignore
        from torch import nn  # type: ignore
    except ImportError as exc:
        raise TemporalTrainingError("PyTorch, NumPy, and ONNX are required in the training environment.") from exc

    training_manifest = read_json(prepared / "training_manifest.json")
    if training_manifest.get("training_purpose") != "auxiliary_collision_timing_pretraining_only":
        raise TemporalTrainingError("An auxiliary-only Nexar prepared manifest is required.")
    if training_manifest.get("promotion_eligible") is not False:
        raise TemporalTrainingError("External pretraining data must be explicitly ineligible for promotion.")
    audit = training_manifest.get("source_audit") if isinstance(training_manifest.get("source_audit"), dict) else {}
    if audit.get("license_accepted") is not True or audit.get("release_evaluation_eligible") is not False:
        raise TemporalTrainingError("Nexar license receipt or evaluation isolation is invalid.")
    if int(audit.get("complete_items") or 0) < 1000:
        raise TemporalTrainingError("The complete verified Nexar training inventory is required.")
    if int(audit.get("positive_items") or 0) < 500 or int(audit.get("hard_negative_items") or 0) < 500:
        raise TemporalTrainingError("Nexar pretraining requires both published positive and negative coverage.")

    feature_manifest = read_json(feature_root / "temporal_features_manifest.json")
    prepared_manifest_sha = sha256(prepared / "training_manifest.json")
    if feature_manifest.get("prepared_manifest_sha256") != prepared_manifest_sha:
        raise TemporalTrainingError("Temporal features do not match the current prepared Nexar manifest.")
    if feature_manifest.get("errors"):
        raise TemporalTrainingError("Nexar feature extraction contains unreadable sequences.")
    all_sequences = [item for item in feature_manifest.get("sequences", []) if isinstance(item, dict)]
    train_sequences = [item for item in all_sequences if item.get("split") == "train"]
    validation_sequences = [item for item in all_sequences if item.get("split") == "validation"]
    public_test_sequences = [item for item in all_sequences if item.get("split") == "test_public"]
    private_lockbox_sequences = [item for item in all_sequences if item.get("split") == "test_private"]
    if len(train_sequences) < 1000 or len(validation_sequences) < 100:
        raise TemporalTrainingError("Verified source-isolated train and validation feature sequences are required.")
    if len(public_test_sequences) != 667:
        raise TemporalTrainingError("The official 667-sequence Nexar public-test split is required.")
    if len(private_lockbox_sequences) != 677:
        raise TemporalTrainingError("The sealed 677-sequence Nexar private-test lockbox is required.")

    class EventTimingPretrainer(nn.Module):
        def __init__(self, features: int):
            super().__init__()
            self.encoder = nn.Sequential(
                nn.Conv1d(features, 64, kernel_size=5, padding=2),
                nn.ReLU(),
                nn.Dropout(0.1),
                nn.Conv1d(64, 64, kernel_size=5, padding=4, dilation=2),
                nn.ReLU(),
                nn.Dropout(0.1),
                nn.Conv1d(64, 64, kernel_size=5, padding=8, dilation=4),
                nn.ReLU(),
                nn.Conv1d(64, 64, kernel_size=5, padding=16, dilation=8),
                nn.ReLU(),
            )
            self.head = nn.Conv1d(64, 1, kernel_size=1)

        def forward(self, values: Any) -> Any:
            return self.head(self.encoder(values.transpose(1, 2))).transpose(1, 2)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    random.seed(TRAINING_SEED)
    np.random.seed(TRAINING_SEED)
    torch.manual_seed(TRAINING_SEED)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(TRAINING_SEED)
    model = EventTimingPretrainer(len(FEATURE_NAMES)).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate, weight_decay=1e-4)
    loss_function = nn.BCEWithLogitsLoss(pos_weight=torch.tensor([8.0], dtype=torch.float32, device=device))
    sequence_loss_function = nn.BCEWithLogitsLoss()

    def calculate_loss(item: dict[str, Any]) -> Any:
        with np.load(str(item["feature_path"])) as payload:
            feature_values = payload["features"].copy()
            times = payload["times"].astype(np.float32)
        features = torch.tensor(feature_values, dtype=torch.float32, device=device).unsqueeze(0)
        targets = np.zeros((len(times), 1), dtype=np.float32)
        event_time = item.get("event_time_sec")
        if event_time is not None:
            alert_time = item.get("alert_time_sec")
            if alert_time is not None and float(alert_time) < float(event_time):
                alert_window = (times >= float(alert_time)) & (times <= float(event_time))
                targets[alert_window, 0] = 1.0
            targets[:, 0] = np.maximum(
                targets[:, 0],
                np.exp(-0.5 * ((times - float(event_time)) / 0.4) ** 2),
            )
        target_tensor = torch.tensor(targets, dtype=torch.float32, device=device).unsqueeze(0)
        logits = model(features)
        frame_loss = loss_function(logits, target_tensor)
        sequence_target = torch.tensor([[float(item.get("event_target") or 0)]], dtype=torch.float32, device=device)
        sequence_loss = sequence_loss_function(logits.max(dim=1).values, sequence_target)
        return frame_loss + 0.5 * sequence_loss

    losses: list[float] = []
    validation_losses: list[float] = []
    best_validation_loss = math.inf
    best_epoch = 0
    best_state: dict[str, Any] | None = None
    for epoch in range(max(1, epochs)):
        model.train()
        epoch_losses = []
        epoch_sequences = list(train_sequences)
        random.Random(TRAINING_SEED + epoch).shuffle(epoch_sequences)
        for item in epoch_sequences:
            optimizer.zero_grad(set_to_none=True)
            loss = calculate_loss(item)
            loss.backward()
            optimizer.step()
            epoch_losses.append(float(loss.detach().cpu()))
        losses.append(statistics.fmean(epoch_losses))
        model.eval()
        with torch.no_grad():
            validation_loss = statistics.fmean(
                float(calculate_loss(item).detach().cpu()) for item in validation_sequences
            )
        validation_losses.append(validation_loss)
        print(f"Auxiliary pretraining epoch {epoch + 1}/{epochs}: validation_loss={validation_loss:.6f}")
        if validation_loss < best_validation_loss:
            best_validation_loss = validation_loss
            best_epoch = epoch + 1
            best_state = {name: value.detach().cpu().clone() for name, value in model.state_dict().items()}

    if best_state is None:
        raise TemporalTrainingError("Auxiliary validation did not produce a usable checkpoint.")
    model.load_state_dict(best_state)

    heldout_records: list[dict[str, Any]] = []
    model.eval()
    with torch.no_grad():
        test_loss = statistics.fmean(float(calculate_loss(item).detach().cpu()) for item in public_test_sequences)
        for item in public_test_sequences:
            with np.load(str(item["feature_path"])) as payload:
                feature_values = payload["features"].copy()
                times = payload["times"].astype(np.float32)
            values = torch.tensor(feature_values, dtype=torch.float32, device=device).unsqueeze(0)
            probabilities = torch.sigmoid(model(values))[0, :, 0].detach().cpu().numpy()
            best_index = int(np.argmax(probabilities))
            event_time = item.get("event_time_sec")
            heldout_records.append(
                {
                    "incident_id": item.get("incident_id"),
                    "target": int(item.get("event_target") or 0),
                    "score": float(probabilities[best_index]),
                    "predicted_time_sec": float(times[best_index]),
                    "event_time_sec": event_time,
                    "time_to_accident_sec": item.get("time_to_accident_sec"),
                    "timing_error_sec": abs(float(times[best_index]) - float(event_time)) if event_time is not None else None,
                }
            )

    positives = [item for item in heldout_records if item["target"] == 1]
    negatives = [item for item in heldout_records if item["target"] == 0]
    favorable_pairs = sum(
        1.0 if positive["score"] > negative["score"] else 0.5 if positive["score"] == negative["score"] else 0.0
        for positive in positives
        for negative in negatives
    )
    auroc = favorable_pairs / max(1, len(positives) * len(negatives))
    ranked = sorted(heldout_records, key=lambda item: float(item["score"]), reverse=True)
    true_positives = 0
    precision_sum = 0.0
    for rank, item in enumerate(ranked, start=1):
        if item["target"] == 1:
            true_positives += 1
            precision_sum += true_positives / rank
    average_precision = precision_sum / max(1, len(positives))
    timing_errors = sorted(
        float(item["timing_error_sec"])
        for item in positives
        if item.get("timing_error_sec") is not None
    )
    p95_index = max(0, math.ceil(len(timing_errors) * 0.95) - 1) if timing_errors else None
    heldout_diagnostics = {
        "source": "nexar_official_public_test",
        "promotion_eligible": False,
        "groups": len(heldout_records),
        "positives": len(positives),
        "negatives": len(negatives),
        "loss": test_loss,
        "auroc": auroc,
        "average_precision": average_precision,
        "recall_at_0_5": sum(1 for item in positives if item["score"] >= 0.5) / max(1, len(positives)),
        "false_positive_rate_at_0_5": sum(1 for item in negatives if item["score"] >= 0.5) / max(1, len(negatives)),
        "timing_error_median_sec": statistics.median(timing_errors) if timing_errors else None,
        "timing_error_p95_sec": timing_errors[p95_index] if p95_index is not None else None,
        "private_lockbox_groups": len(private_lockbox_sequences),
        "private_lockbox_evaluated": False,
    }
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    run_dir = output / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    checkpoint = run_dir / "nexar_event_timing_pretrainer.pt"
    torch.save(
        {
            "state_dict": model.state_dict(),
            "encoder_state_dict": model.encoder.state_dict(),
            "feature_names": FEATURE_NAMES,
            "training_role": "auxiliary_collision_timing_pretraining_only",
        },
        checkpoint,
    )
    onnx_path = run_dir / "nexar_event_timing_pretrainer.onnx"
    model.eval().cpu()
    try:
        torch.onnx.export(
            model,
            torch.zeros((1, 32, len(FEATURE_NAMES)), dtype=torch.float32),
            onnx_path,
            input_names=["features"],
            output_names=["collision_or_near_miss_event_logits"],
            dynamic_axes={"features": {1: "time"}, "collision_or_near_miss_event_logits": {1: "time"}},
            opset_version=17,
            dynamo=False,
        )
    except Exception as exc:
        write_json(
            run_dir / "run_manifest.json",
            {
                "schema_version": "mimir_training_run_v1",
                "run_id": run_id,
                "completed_at": utc_now(),
                "candidate_kind": "temporal_collision_timing_pretrainer",
                "training_role": "auxiliary_only",
                "promotion_eligible": False,
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
                "checkpoint": {"path": str(checkpoint), "sha256": sha256(checkpoint)},
            },
        )
        raise
    candidate_id = f"mimir-nexar-event-pretrainer-{run_id}"
    model_manifest = {
        "schema_version": "mimir_candidate_model_manifest_v1",
        "candidate_id": candidate_id,
        "candidate_kind": "temporal_collision_timing_pretrainer",
        "created_at": utc_now(),
        "model_path": str(onnx_path.resolve()),
        "checkpoint_path": str(checkpoint.resolve()),
        "sha256": sha256(onnx_path),
        "checkpoint_sha256": sha256(checkpoint),
        "license": "Nexar Open Data License; attribution and no-resale conditions apply",
        "attribution": "Moura, Daniel C., Shizhan Zhu, and Orly Zvitia. Nexar Dashcam Collision Prediction Dataset and Challenge (2025).",
        "promoted": False,
        "promotion_eligible": False,
        "training_role": "auxiliary_encoder_initialization_only",
        "feature_version": FEATURE_VERSION,
        "temporal_architecture": TEMPORAL_ARCHITECTURE,
        "feature_names": list(FEATURE_NAMES),
        "outputs": ["collision_or_near_miss_event_probability"],
        "limitations": [
            "Positive labels combine collisions and near-misses.",
            "Moving dashcam geometry is not Tesla parked-camera contact geometry.",
            "A consented Tesla fine-tune and locked Tesla evaluation are required before promotion.",
        ],
        "heldout_nexar_diagnostics": heldout_diagnostics,
    }
    run_manifest = {
        "schema_version": "mimir_training_run_v1",
        "run_id": run_id,
        "started_at": utc_now(),
        "completed_at": utc_now(),
        "candidate_kind": "temporal_collision_timing_pretrainer",
        "training_role": "auxiliary_only",
        "promotion_eligible": False,
        "dataset_manifest_sha256": sha256(prepared / "training_manifest.json"),
        "feature_manifest_sha256": sha256(feature_root / "temporal_features_manifest.json"),
        "status": "complete",
        "cuda_device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "epochs": epochs,
        "learning_rate": learning_rate,
        "training_seed": TRAINING_SEED,
        "loss_history": losses,
        "validation_loss_history": validation_losses,
        "best_validation_loss": best_validation_loss,
        "best_epoch": best_epoch,
        "heldout_nexar_diagnostics": heldout_diagnostics,
        "artifacts": [
            {"path": str(checkpoint), "sha256": sha256(checkpoint)},
            {"path": str(onnx_path), "sha256": sha256(onnx_path)},
        ],
    }
    write_json(run_dir / "candidate_model_manifest.json", model_manifest)
    write_json(run_dir / "run_manifest.json", run_manifest)
    write_json(
        run_dir / "nexar_heldout_evaluation.json",
        {"summary": heldout_diagnostics, "items": heldout_records},
    )
    return {"run": run_manifest, "model": model_manifest}


def predict_temporal_candidate(
    feature_root: Path,
    model_manifest_path: Path,
    output: Path,
    split: str = "test",
    training_diagnostics_only: bool = False,
) -> dict[str, Any]:
    """Run a frozen candidate policy and produce evaluation-ready predictions."""
    try:
        import numpy as np  # type: ignore
        import onnxruntime as ort  # type: ignore
    except ImportError as exc:
        raise TemporalTrainingError("NumPy and ONNX Runtime are required for candidate inference.") from exc
    manifest = read_json(model_manifest_path)
    if manifest.get("candidate_kind") != "temporal_contact_verifier" or manifest.get("promoted") is not False:
        raise TemporalTrainingError("An explicitly unpromoted temporal candidate manifest is required.")
    model_path = Path(str(manifest.get("model_path") or ""))
    if not model_path.is_absolute():
        model_path = model_manifest_path.resolve().parent / model_path
    if not model_path.is_file() or sha256(model_path) != str(manifest.get("sha256") or ""):
        raise TemporalTrainingError("Candidate model file is missing or does not match its manifest checksum.")
    feature_manifest_path = feature_root / "temporal_features_manifest.json"
    feature_manifest = read_json(feature_manifest_path)
    sequences = [
        item
        for item in feature_manifest.get("sequences", [])
        if isinstance(item, dict) and (split == "all" or item.get("split") == split)
    ]
    if not sequences:
        raise TemporalTrainingError(f"No temporal sequences are available for split {split!r}.")
    oracle_sequences = [
        str(item.get("incident_id") or "")
        for item in sequences
        if item.get("geometry_provenance") != "candidate_segmentation"
    ]
    if oracle_sequences and not training_diagnostics_only:
        raise TemporalTrainingError(
            "Locked evaluation cannot use human annotation geometry as model input. "
            "Run the segmentation candidate first and extract candidate_segmentation features."
        )
    policy = manifest.get("decision_policy") if isinstance(manifest.get("decision_policy"), dict) else {}
    if policy.get("frozen_before_locked_evaluation") is not True:
        raise TemporalTrainingError("Candidate decision policy was not frozen before locked evaluation.")
    contact_threshold = float(policy.get("contact_review_probability") or 0.5)
    impact_threshold = float(policy.get("impact_important_probability") or 0.5)
    available = ort.get_available_providers()
    providers = [name for name in ("CUDAExecutionProvider", "CPUExecutionProvider") if name in available]
    session = ort.InferenceSession(str(model_path), providers=providers or ["CPUExecutionProvider"])
    input_name = session.get_inputs()[0].name
    predictions: list[dict[str, Any]] = []
    for item in sequences:
        with np.load(str(item["feature_path"])) as payload:
            features = payload["features"].astype(np.float32)
            times = payload["times"].astype(np.float32)
        outputs = session.run(None, {input_name: features[None, ...]})
        logits = np.asarray(outputs[0], dtype=np.float32)[0]
        probabilities = 1.0 / (1.0 + np.exp(-np.clip(logits, -40.0, 40.0)))
        contact = probability_timing(times.tolist(), probabilities[:, 0].tolist())
        impact = probability_timing(times.tolist(), probabilities[:, 1].tolist())
        impact_probability = float(impact["probability"] or 0.0)
        contact_probability = float(contact["probability"] or 0.0)
        if impact_probability >= impact_threshold:
            predicted_severity = "IMPORTANT"
            timing = impact
        elif contact_probability >= contact_threshold:
            predicted_severity = "REVIEW"
            timing = contact
        else:
            predicted_severity = "IGNORE"
            timing = impact if impact_probability >= contact_probability else contact
        predictions.append(
            {
                "incident_id": item.get("incident_id"),
                "source_group_hash": item.get("source_group_hash"),
                "split": item.get("split"),
                "severity": predicted_severity,
                "contact_probability": contact_probability,
                "impact_probability": impact_probability,
                "best_time_sec": timing.get("best_time_sec"),
                "timing_uncertainty_sec": timing.get("timing_uncertainty_sec"),
                "model_version": manifest.get("candidate_id"),
                "model_sha256": manifest.get("sha256"),
                "evidence_provenance": item.get("geometry_provenance"),
            }
        )
    result = {
        "schema_version": "mimir_candidate_predictions_v1",
        "created_at": utc_now(),
        "candidate_id": manifest.get("candidate_id"),
        "candidate_manifest_sha256": sha256(model_manifest_path),
        "model_sha256": manifest.get("sha256"),
        "feature_manifest_sha256": sha256(feature_manifest_path),
        "split": split,
        "release_eligible": not oracle_sequences and not training_diagnostics_only,
        "training_diagnostics_only": training_diagnostics_only,
        "oracle_geometry_incidents": oracle_sequences,
        "decision_policy": policy,
        "items": predictions,
    }
    write_json(output, result)
    return result


def probability_timing(times: Iterable[float], probabilities: Iterable[float]) -> dict[str, float | None]:
    pairs = [(float(time), max(0.0, min(1.0, float(probability)))) for time, probability in zip(times, probabilities)]
    if not pairs:
        return {"probability": 0.0, "best_time_sec": None, "timing_uncertainty_sec": None}
    best_time, best_probability = max(pairs, key=lambda value: value[1])
    total = sum(value[1] for value in pairs)
    if total <= 1e-9:
        uncertainty = None
    else:
        mean = sum(time * probability for time, probability in pairs) / total
        uncertainty = math.sqrt(sum(probability * (time - mean) ** 2 for time, probability in pairs) / total)
    return {
        "probability": round(best_probability, 6),
        "best_time_sec": round(best_time, 4),
        "timing_uncertainty_sec": round(uncertainty, 4) if uncertainty is not None else None,
    }
