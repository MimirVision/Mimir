"""Train a quarantined temporal initializer on CARLA collision ground truth.

This module never runs in the production scanner. Simulator labels provide
exact synthetic collision timing, but they do not measure real Tesla accuracy.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .temporal_training import FEATURE_NAMES, TRAINING_SEED, read_json, sha256, write_json


MODEL_VERSION = "mimir_carla_collision_timing_shadow_v1"


class CarlaAuxiliaryError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _load_sequence(item: dict[str, Any]) -> tuple[Any, Any]:
    try:
        import numpy as np  # type: ignore
    except ImportError as exc:
        raise CarlaAuxiliaryError("NumPy is required for CARLA auxiliary training") from exc
    with np.load(str(item["feature_path"])) as payload:
        return payload["features"].copy(), payload["times"].astype(np.float32)


def _classification_metrics(rows: list[dict[str, Any]], threshold: float) -> dict[str, Any]:
    true_positive = sum(1 for item in rows if item["target"] == 1 and item["score"] >= threshold)
    false_negative = sum(1 for item in rows if item["target"] == 1 and item["score"] < threshold)
    false_positive = sum(1 for item in rows if item["target"] == 0 and item["score"] >= threshold)
    true_negative = sum(1 for item in rows if item["target"] == 0 and item["score"] < threshold)
    precision = true_positive / max(true_positive + false_positive, 1)
    recall = true_positive / max(true_positive + false_negative, 1)
    specificity = true_negative / max(true_negative + false_positive, 1)
    return {
        "count": len(rows),
        "threshold": round(threshold, 6),
        "true_positive": true_positive,
        "false_negative": false_negative,
        "false_positive": false_positive,
        "true_negative": true_negative,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "specificity": round(specificity, 4),
        "balanced_accuracy": round((recall + specificity) / 2.0, 4),
        "f1": round(2.0 * precision * recall / max(precision + recall, 1e-12), 4),
    }


def _choose_threshold(rows: list[dict[str, Any]]) -> float:
    candidates = sorted({0.05, 0.5, 0.95, *(float(item["score"]) for item in rows)})
    ranked = []
    for threshold in candidates:
        metrics = _classification_metrics(rows, threshold)
        ranked.append(
            (
                float(metrics["balanced_accuracy"]),
                float(metrics["f1"]),
                -float(metrics["false_positive"]),
                threshold,
            )
        )
    return max(ranked)[-1] if ranked else 0.5


def train_carla_auxiliary(
    feature_root: Path,
    prepared: Path,
    output: Path,
    epochs: int = 18,
    learning_rate: float = 0.001,
    min_sequences: int = 30,
) -> dict[str, Any]:
    try:
        import numpy as np  # type: ignore
        import torch  # type: ignore
        from torch import nn  # type: ignore
    except ImportError as exc:
        raise CarlaAuxiliaryError(
            "PyTorch, NumPy, and ONNX are required for CARLA auxiliary training"
        ) from exc

    training_manifest = read_json(prepared / "training_manifest.json")
    if (
        training_manifest.get("training_purpose")
        != "synthetic_stationary_collision_timing_pretraining_only"
    ):
        raise CarlaAuxiliaryError("A CARLA synthetic auxiliary manifest is required")
    if training_manifest.get("promotion_eligible") is not False:
        raise CarlaAuxiliaryError("CARLA promotion quarantine is missing")
    source_audit = (
        training_manifest.get("source_audit")
        if isinstance(training_manifest.get("source_audit"), dict)
        else {}
    )
    if source_audit.get("simulated_collision_ground_truth") is not True:
        raise CarlaAuxiliaryError("CARLA collision-sensor provenance is missing")
    if source_audit.get("real_world_contact_ground_truth") is not False:
        raise CarlaAuxiliaryError("CARLA data must not claim real-world contact ground truth")

    feature_manifest = read_json(feature_root / "temporal_features_manifest.json")
    if feature_manifest.get("prepared_manifest_sha256") != sha256(
        prepared / "training_manifest.json"
    ):
        raise CarlaAuxiliaryError("CARLA temporal features do not match the training manifest")
    if feature_manifest.get("errors"):
        raise CarlaAuxiliaryError("CARLA feature extraction contains unreadable sequences")
    sequences = [
        item for item in feature_manifest.get("sequences", []) if isinstance(item, dict)
    ]
    if len(sequences) < min_sequences:
        raise CarlaAuxiliaryError(
            f"CARLA shadow training requires at least {min_sequences} sequences"
        )
    grouped = {
        split: [item for item in sequences if item.get("split") == split]
        for split in ("train", "validation", "test")
    }
    for split, items in grouped.items():
        targets = {int(item.get("event_target") or 0) for item in items}
        if not items or targets != {0, 1}:
            raise CarlaAuxiliaryError(
                f"CARLA {split} split must contain collision and no-collision sequences"
            )

    class CollisionTimingNet(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.encoder = nn.Sequential(
                nn.Conv1d(len(FEATURE_NAMES), 64, kernel_size=5, padding=2),
                nn.SiLU(),
                nn.Dropout(0.1),
                nn.Conv1d(64, 64, kernel_size=5, padding=4, dilation=2),
                nn.SiLU(),
                nn.Conv1d(64, 64, kernel_size=5, padding=8, dilation=4),
                nn.SiLU(),
                nn.Conv1d(64, 64, kernel_size=5, padding=16, dilation=8),
                nn.SiLU(),
            )
            self.head = nn.Conv1d(64, 1, kernel_size=1)

        def forward(self, values: Any) -> Any:
            return self.head(self.encoder(values.transpose(1, 2))).transpose(1, 2)

    random.seed(TRAINING_SEED)
    np.random.seed(TRAINING_SEED)
    torch.manual_seed(TRAINING_SEED)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(TRAINING_SEED)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = CollisionTimingNet().to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate, weight_decay=1e-4)
    frame_loss = nn.BCEWithLogitsLoss(
        pos_weight=torch.tensor([8.0], dtype=torch.float32, device=device)
    )
    sequence_loss = nn.BCEWithLogitsLoss()

    def calculate_loss(item: dict[str, Any]) -> Any:
        feature_values, times = _load_sequence(item)
        features = torch.tensor(
            feature_values, dtype=torch.float32, device=device
        ).unsqueeze(0)
        targets = np.zeros((len(times), 1), dtype=np.float32)
        event_time = item.get("event_time_sec")
        if event_time is not None:
            targets[:, 0] = np.exp(
                -0.5 * ((times - float(event_time)) / 0.35) ** 2
            )
        target_tensor = torch.tensor(
            targets, dtype=torch.float32, device=device
        ).unsqueeze(0)
        logits = model(features)
        per_frame = frame_loss(logits, target_tensor)
        sequence_target = torch.tensor(
            [[float(item.get("event_target") or 0)]],
            dtype=torch.float32,
            device=device,
        )
        per_sequence = sequence_loss(logits.max(dim=1).values, sequence_target)
        return per_frame + 0.5 * per_sequence

    history: list[dict[str, float | int]] = []
    best_loss = math.inf
    best_epoch = 0
    best_state: dict[str, Any] | None = None
    stale_epochs = 0
    started = time.perf_counter()
    for epoch in range(1, max(1, epochs) + 1):
        model.train()
        train_items = list(grouped["train"])
        random.Random(TRAINING_SEED + epoch).shuffle(train_items)
        losses = []
        for item in train_items:
            optimizer.zero_grad(set_to_none=True)
            loss = calculate_loss(item)
            loss.backward()
            optimizer.step()
            losses.append(float(loss.detach().cpu()))
        model.eval()
        with torch.no_grad():
            validation_losses = [
                float(calculate_loss(item).detach().cpu())
                for item in grouped["validation"]
            ]
        train_mean = statistics.fmean(losses)
        validation_mean = statistics.fmean(validation_losses)
        history.append(
            {
                "epoch": epoch,
                "training_loss": round(train_mean, 6),
                "validation_loss": round(validation_mean, 6),
            }
        )
        print(
            f"CARLA epoch {epoch}/{epochs}: train={train_mean:.5f} "
            f"validation={validation_mean:.5f}"
        )
        if validation_mean < best_loss - 1e-5:
            best_loss = validation_mean
            best_epoch = epoch
            best_state = {
                name: value.detach().cpu().clone()
                for name, value in model.state_dict().items()
            }
            stale_epochs = 0
        else:
            stale_epochs += 1
            if stale_epochs >= 5 and epoch >= 8:
                break
    if best_state is None:
        raise CarlaAuxiliaryError("CARLA training did not produce a checkpoint")
    model.load_state_dict(best_state)
    model.to(device)
    model.eval()

    def predict(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows = []
        with torch.no_grad():
            for item in items:
                feature_values, times = _load_sequence(item)
                values = torch.tensor(
                    feature_values, dtype=torch.float32, device=device
                ).unsqueeze(0)
                probabilities = torch.sigmoid(model(values))[0, :, 0].cpu().numpy()
                best_index = int(np.argmax(probabilities))
                event_time = item.get("event_time_sec")
                rows.append(
                    {
                        "incident_id": item.get("incident_id"),
                        "target": int(item.get("event_target") or 0),
                        "score": round(float(probabilities[best_index]), 7),
                        "predicted_time_sec": round(float(times[best_index]), 4),
                        "event_time_sec": event_time,
                        "timing_error_sec": (
                            round(abs(float(times[best_index]) - float(event_time)), 4)
                            if event_time is not None
                            else None
                        ),
                    }
                )
        return rows

    validation_rows = predict(grouped["validation"])
    threshold = _choose_threshold(validation_rows)
    test_rows = predict(grouped["test"])
    timing_errors = sorted(
        float(item["timing_error_sec"])
        for item in test_rows
        if item.get("timing_error_sec") is not None
    )
    p95_index = (
        max(0, math.ceil(0.95 * len(timing_errors)) - 1)
        if timing_errors
        else None
    )
    validation_metrics = _classification_metrics(validation_rows, threshold)
    test_metrics = _classification_metrics(test_rows, threshold)
    test_metrics["timing_error_median_sec"] = (
        round(statistics.median(timing_errors), 4) if timing_errors else None
    )
    test_metrics["timing_error_p95_sec"] = (
        round(timing_errors[p95_index], 4) if p95_index is not None else None
    )

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = output / run_id
    suffix = 1
    while run_dir.exists():
        run_dir = output / f"{run_id}-{suffix}"
        suffix += 1
    run_dir.mkdir(parents=True, exist_ok=False)
    checkpoint = run_dir / "carla_collision_timing_shadow.pt"
    torch.save(
        {
            "state_dict": best_state,
            "encoder_state_dict": {
                name.removeprefix("encoder."): value
                for name, value in best_state.items()
                if name.startswith("encoder.")
            },
            "feature_names": FEATURE_NAMES,
            "model_version": MODEL_VERSION,
            "training_role": "synthetic_auxiliary_only",
        },
        checkpoint,
    )
    onnx_path = run_dir / "carla_collision_timing_shadow.onnx"
    model.cpu().eval()
    torch.onnx.export(
        model,
        torch.zeros((1, 70, len(FEATURE_NAMES)), dtype=torch.float32),
        onnx_path,
        input_names=["features"],
        output_names=["synthetic_collision_logits"],
        dynamic_axes={
            "features": {1: "time"},
            "synthetic_collision_logits": {1: "time"},
        },
        opset_version=17,
        dynamo=False,
    )
    try:
        import onnx  # type: ignore

        onnx.checker.check_model(onnx.load(str(onnx_path)))
    except Exception as exc:
        raise CarlaAuxiliaryError(f"CARLA ONNX validation failed: {exc}") from exc
    try:
        import onnxruntime as ort  # type: ignore

        parity_features, _ = _load_sequence(grouped["validation"][0])
        parity_input = parity_features.astype("float32")[None, :, :]
        with torch.no_grad():
            torch_logits = model(torch.from_numpy(parity_input)).numpy()
        session = ort.InferenceSession(
            str(onnx_path),
            providers=["CPUExecutionProvider"],
        )
        onnx_logits = session.run(
            ["synthetic_collision_logits"],
            {"features": parity_input},
        )[0]
        onnx_max_abs_error = float(np.max(np.abs(torch_logits - onnx_logits)))
        if not np.isfinite(onnx_max_abs_error) or onnx_max_abs_error > 1e-4:
            raise CarlaAuxiliaryError(
                "CARLA ONNX parity failed: max absolute error "
                f"{onnx_max_abs_error:.8f}"
            )
    except CarlaAuxiliaryError:
        raise
    except Exception as exc:
        raise CarlaAuxiliaryError(
            f"CARLA ONNX Runtime smoke test failed: {exc}"
        ) from exc

    predictions_path = run_dir / "heldout_predictions.json"
    write_json(
        predictions_path,
        {
            "schema_version": "mimir_carla_heldout_predictions_v1",
            "threshold_selected_on_validation": threshold,
            "validation": validation_rows,
            "test": test_rows,
        },
    )
    result = {
        "schema_version": "mimir_candidate_model_manifest_v1",
        "candidate_id": f"mimir-carla-shadow-{run_dir.name}",
        "candidate_kind": "synthetic_collision_timing_pretrainer",
        "model_version": MODEL_VERSION,
        "created_at": utc_now(),
        "production_detector": False,
        "promoted": False,
        "promotion_eligible": False,
        "training_role": "synthetic_auxiliary_encoder_initialization_only",
        "real_world_contact_ground_truth": False,
        "simulated_collision_ground_truth": True,
        "model_path": str(onnx_path.resolve()),
        "model_sha256": sha256(onnx_path),
        "onnx_runtime_parity_max_abs_error": round(onnx_max_abs_error, 10),
        "checkpoint_path": str(checkpoint.resolve()),
        "checkpoint_sha256": sha256(checkpoint),
        "feature_names": list(FEATURE_NAMES),
        "dataset_manifest": str((prepared / "training_manifest.json").resolve()),
        "dataset_manifest_sha256": sha256(prepared / "training_manifest.json"),
        "feature_manifest_sha256": sha256(
            feature_root / "temporal_features_manifest.json"
        ),
        "device": str(device),
        "best_epoch": best_epoch,
        "best_validation_loss": round(best_loss, 6),
        "history": history,
        "threshold_selected_on_validation": round(threshold, 6),
        "validation_metrics": validation_metrics,
        "heldout_synthetic_test_metrics": test_metrics,
        "predictions_path": str(predictions_path.resolve()),
        "predictions_sha256": sha256(predictions_path),
        "runtime_sec": round(time.perf_counter() - started, 3),
        "license": {
            "CARLA_code": "MIT",
            "CARLA_assets": "CC-BY-4.0",
        },
        "limitations": [
            "Synthetic rendering and physics differ from real Tesla Sentry footage.",
            "Synthetic test metrics are not release evidence.",
            "A consented real-data fine-tune and locked Tesla evaluation are required.",
        ],
    }
    result["manifest_checksum"] = hashlib.sha256(
        json.dumps(result, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    write_json(run_dir / "candidate_model_manifest.json", result)
    return result
