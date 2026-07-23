"""Generate quarantined synthetic parked-vehicle impact sequences with CARLA.

The generator records RGB video and exact simulator collision events. Its
output is auxiliary-only and can never promote a production contact model
without consented real Tesla footage and locked target-domain evaluation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import queue
import random
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "mimir_carla_stationary_collision_v1"
GENERATOR_VERSION = "mimir_carla_stationary_generator_v2"
VALIDATED_TOWN05_SPAWN_INDICES = (248,)
SPLIT_VERSION = "carla_scenario_sha256_60_20_20_v2"
CARLA_VERSION = "0.9.16"
FPS = 10
WIDTH = 640
HEIGHT = 360
SCENARIO_DURATION_SEC = 7.0
ACTION_START_SEC = 1.5
POSITIVE_SCENARIOS = ("rear_impact", "front_impact", "side_impact")
NEGATIVE_SCENARIOS = ("rear_near_miss", "side_near_miss", "neighbor_door_activity")
ALL_SCENARIOS = POSITIVE_SCENARIOS + NEGATIVE_SCENARIOS
STABLE_PILOT_SCENARIOS = ("rear_impact", "front_impact", "rear_near_miss")


class CarlaGenerationError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def split_for_scenario(scenario_id: str) -> str:
    bucket = int(hashlib.sha256(scenario_id.encode("utf-8")).hexdigest()[:8], 16) % 10
    if bucket < 2:
        return "test"
    if bucket < 4:
        return "validation"
    return "train"


def _install_carla_import(carla_root: Path) -> None:
    candidates = [
        *carla_root.glob("PythonAPI/carla/dist/carla-*.egg"),
        *carla_root.rglob("carla-*.egg"),
    ]
    for candidate in candidates:
        sys.path.insert(0, str(candidate))
    python_api = carla_root / "PythonAPI" / "carla"
    if python_api.is_dir():
        sys.path.insert(0, str(python_api.parent))


def import_carla(carla_root: Path) -> Any:
    try:
        import carla  # type: ignore
        return carla
    except ImportError:
        _install_carla_import(carla_root)
        try:
            import carla  # type: ignore
        except ImportError as exc:
            available = sorted(str(path) for path in carla_root.rglob("carla-*"))
            raise CarlaGenerationError(
                "CARLA Python API could not be imported. Install the matching wheel "
                f"from the extracted package. Found: {available[:8]}"
            ) from exc
    return carla


def find_server(carla_root: Path) -> Path:
    preferred = (
        carla_root / "CarlaUE4.exe",
        carla_root / "WindowsNoEditor" / "CarlaUE4.exe",
    )
    for candidate in preferred:
        if candidate.is_file():
            return candidate
    candidates = sorted(carla_root.rglob("CarlaUE4.exe"))
    if not candidates:
        raise CarlaGenerationError(f"CarlaUE4.exe was not found under {carla_root}")
    return candidates[0]


def launch_server(carla_root: Path, port: int, log_path: Path) -> subprocess.Popen[Any]:
    executable = find_server(carla_root)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = log_path.open("w", encoding="utf-8")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    command = [
        str(executable),
        "-RenderOffScreen",
        "-quality-level=Low",
        "-nosound",
        f"-carla-rpc-port={port}",
        f"-ResX={WIDTH}",
        f"-ResY={HEIGHT}",
    ]
    process = subprocess.Popen(
        command,
        cwd=str(executable.parent),
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        creationflags=flags,
    )
    setattr(process, "_mimir_log_handle", log_handle)
    return process


def wait_for_server(carla: Any, port: int, timeout_sec: float) -> Any:
    deadline = time.monotonic() + timeout_sec
    last_error = "not started"
    while time.monotonic() < deadline:
        try:
            client = carla.Client("127.0.0.1", port)
            client.set_timeout(5.0)
            client.get_world()
            return client
        except RuntimeError as exc:
            last_error = str(exc)
            time.sleep(2.0)
    raise CarlaGenerationError(f"CARLA server did not become ready: {last_error}")


def vector_length(value: Any) -> float:
    return math.sqrt(float(value.x) ** 2 + float(value.y) ** 2 + float(value.z) ** 2)


def scaled_vector(carla: Any, value: Any, scale: float) -> Any:
    return carla.Vector3D(
        x=float(value.x) * scale,
        y=float(value.y) * scale,
        z=float(value.z) * scale,
    )


def translated_transform(
    carla: Any,
    transform: Any,
    forward_m: float = 0.0,
    right_m: float = 0.0,
    yaw_delta: float = 0.0,
) -> Any:
    forward = transform.get_forward_vector()
    right = transform.get_right_vector()
    location = carla.Location(
        x=float(transform.location.x) + float(forward.x) * forward_m + float(right.x) * right_m,
        y=float(transform.location.y) + float(forward.y) * forward_m + float(right.y) * right_m,
        z=float(transform.location.z) + 0.15,
    )
    rotation = carla.Rotation(
        pitch=float(transform.rotation.pitch),
        yaw=float(transform.rotation.yaw) + yaw_delta,
        roll=float(transform.rotation.roll),
    )
    return carla.Transform(location, rotation)


def camera_transform(carla: Any, camera: str) -> Any:
    transforms = {
        "front": ((2.05, 0.0, 1.35), (0.0, 0.0, 0.0)),
        "rear": ((-2.0, 0.0, 1.3), (0.0, 180.0, 0.0)),
        "left_repeater": ((0.2, -0.92, 1.22), (0.0, -92.0, 0.0)),
        "right_repeater": ((0.2, 0.92, 1.22), (0.0, 92.0, 0.0)),
    }
    location, rotation = transforms[camera]
    return carla.Transform(
        carla.Location(x=location[0], y=location[1], z=location[2]),
        carla.Rotation(pitch=rotation[0], yaw=rotation[1], roll=rotation[2]),
    )


def scenario_camera(kind: str, side_sign: int) -> str:
    if kind.startswith("rear_"):
        return "rear"
    if kind.startswith("front_"):
        return "front"
    return "right_repeater" if side_sign > 0 else "left_repeater"


@dataclass
class ScenarioGeometry:
    target_transform: Any
    velocity: Any
    camera: str
    speed_mps: float
    neighbor_door: bool = False


def geometry_for_scenario(
    carla: Any,
    kind: str,
    ego_transform: Any,
    rng: random.Random,
) -> ScenarioGeometry:
    forward = ego_transform.get_forward_vector()
    right = ego_transform.get_right_vector()
    side_sign = rng.choice((-1, 1))
    if kind == "rear_impact":
        speed = rng.uniform(3.0, 6.5)
        return ScenarioGeometry(
            translated_transform(carla, ego_transform, forward_m=-rng.uniform(10.0, 15.0)),
            scaled_vector(carla, forward, speed),
            "rear",
            speed,
        )
    if kind == "front_impact":
        speed = rng.uniform(2.5, 5.0)
        return ScenarioGeometry(
            translated_transform(
                carla,
                ego_transform,
                forward_m=rng.uniform(9.0, 13.0),
                yaw_delta=180.0,
            ),
            scaled_vector(carla, forward, -speed),
            "front",
            speed,
        )
    if kind == "side_impact":
        speed = rng.uniform(2.0, 4.5)
        return ScenarioGeometry(
            translated_transform(
                carla,
                ego_transform,
                right_m=side_sign * rng.uniform(7.0, 10.0),
                yaw_delta=-90.0 * side_sign,
            ),
            scaled_vector(carla, right, -side_sign * speed),
            scenario_camera(kind, side_sign),
            speed,
        )
    if kind == "rear_near_miss":
        speed = rng.uniform(3.0, 6.5)
        return ScenarioGeometry(
            translated_transform(
                carla,
                ego_transform,
                forward_m=-rng.uniform(10.0, 15.0),
                right_m=side_sign * rng.uniform(3.2, 4.5),
            ),
            scaled_vector(carla, forward, speed),
            "rear",
            speed,
        )
    if kind == "side_near_miss":
        speed = rng.uniform(2.0, 5.0)
        return ScenarioGeometry(
            translated_transform(
                carla,
                ego_transform,
                forward_m=rng.uniform(4.5, 6.5),
                right_m=side_sign * rng.uniform(8.0, 11.0),
                yaw_delta=-90.0 * side_sign,
            ),
            scaled_vector(carla, right, -side_sign * speed),
            scenario_camera(kind, side_sign),
            speed,
        )
    if kind == "neighbor_door_activity":
        return ScenarioGeometry(
            translated_transform(
                carla,
                ego_transform,
                forward_m=rng.uniform(-0.8, 0.8),
                right_m=side_sign * rng.uniform(3.2, 4.2),
            ),
            carla.Vector3D(),
            scenario_camera(kind, side_sign),
            0.0,
            neighbor_door=True,
        )
    raise CarlaGenerationError(f"Unsupported CARLA scenario: {kind}")


def choose_vehicle_blueprints(world: Any, rng: random.Random) -> tuple[Any, Any]:
    library = world.get_blueprint_library()
    ego_candidates = list(library.filter("vehicle.tesla.model3"))
    stable_target_ids = (
        "vehicle.chevrolet.impala",
        "vehicle.lincoln.mkz_2017",
    )
    vehicle_candidates = []
    for blueprint in library.filter("vehicle.*"):
        wheels = blueprint.get_attribute("number_of_wheels").as_int() if blueprint.has_attribute("number_of_wheels") else 4
        if wheels == 4 and not blueprint.id.endswith(("ambulance", "firetruck")):
            vehicle_candidates.append(blueprint)
    if not vehicle_candidates:
        raise CarlaGenerationError("CARLA has no suitable four-wheel vehicle blueprints")
    ego = ego_candidates[0] if ego_candidates else rng.choice(vehicle_candidates)
    stable_targets = [
        library.find(blueprint_id)
        for blueprint_id in stable_target_ids
        if library.find(blueprint_id) is not None
    ]
    target_pool = [
        item
        for item in (stable_targets or vehicle_candidates)
        if item.id != ego.id
    ]
    target = rng.choice(target_pool or vehicle_candidates)
    for blueprint in (ego, target):
        if blueprint.has_attribute("role_name"):
            blueprint.set_attribute("role_name", "mimir_synthetic")
        if blueprint.has_attribute("color"):
            colors = blueprint.get_attribute("color").recommended_values
            if colors:
                blueprint.set_attribute("color", rng.choice(colors))
    return ego, target


def get_sensor_frame(
    sensor_queue: queue.Queue[Any],
    frame: int,
    stream_name: str,
    timeout: float = 60.0,
) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining = max(0.05, deadline - time.monotonic())
        try:
            data = sensor_queue.get(timeout=remaining)
        except queue.Empty:
            continue
        if int(data.frame) >= frame:
            return data
    raise CarlaGenerationError(
        f"Timed out waiting for {stream_name} sensor frame {frame}"
    )


def convert_image(image: Any) -> Any:
    try:
        import numpy as np  # type: ignore
    except ImportError as exc:
        raise CarlaGenerationError("NumPy is required for CARLA generation") from exc
    pixels = np.frombuffer(image.raw_data, dtype=np.uint8).reshape((image.height, image.width, 4))
    return pixels[:, :, :3].copy()


def _bbox_from_mask(mask: Any) -> list[int] | None:
    try:
        import numpy as np  # type: ignore
    except ImportError as exc:
        raise CarlaGenerationError("NumPy is required for CARLA masks") from exc
    y_values, x_values = np.nonzero(mask)
    if not len(x_values):
        return None
    return [
        int(x_values.min()),
        int(y_values.min()),
        int(x_values.max()) + 1,
        int(y_values.max()) + 1,
    ]


def decode_actor_masks(image: Any, ego_id: int, target_id: int) -> tuple[Any, Any, str]:
    """Decode CARLA's lossless G/B actor ID channels without assuming byte order."""
    try:
        import numpy as np  # type: ignore
    except ImportError as exc:
        raise CarlaGenerationError("NumPy is required for CARLA masks") from exc
    pixels = np.frombuffer(image.raw_data, dtype=np.uint8).reshape((image.height, image.width, 4))
    blue = pixels[:, :, 0].astype(np.uint16)
    green = pixels[:, :, 1].astype(np.uint16)
    candidates = (
        ("green_high_blue_low", (green << 8) | blue),
        ("blue_high_green_low", (blue << 8) | green),
    )
    best = max(
        candidates,
        key=lambda item: int((item[1] == ego_id).sum()) + int((item[1] == target_id).sum()),
    )
    actor_ids = best[1]
    return actor_ids == ego_id, actor_ids == target_id, best[0]


def mask_geometry(ego_mask: Any, target_mask: Any) -> dict[str, Any]:
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except ImportError as exc:
        raise CarlaGenerationError("OpenCV and NumPy are required for CARLA masks") from exc
    ego_pixels = int(ego_mask.sum())
    target_pixels = int(target_mask.sum())
    distance = None
    adjacent = False
    if ego_pixels and target_pixels:
        dilated_ego = cv2.dilate(ego_mask.astype(np.uint8), np.ones((3, 3), np.uint8))
        adjacent = bool((dilated_ego.astype(bool) & target_mask).any())
        if adjacent:
            distance = 0.0
        else:
            distance_map = cv2.distanceTransform(
                (~ego_mask).astype(np.uint8),
                cv2.DIST_L2,
                3,
            )
            distance = float(distance_map[target_mask].min())
    return {
        "ego_bbox_xyxy": _bbox_from_mask(ego_mask),
        "target_bbox_xyxy": _bbox_from_mask(target_mask),
        "ego_pixels": ego_pixels,
        "target_pixels": target_pixels,
        "mask_distance_pixels": round(distance, 4) if distance is not None else None,
        "masks_adjacent": adjacent,
    }


def run_scenario(
    carla: Any,
    world: Any,
    output_root: Path,
    scenario_index: int,
    kind: str,
    seed: int,
    server_process: subprocess.Popen[Any] | None = None,
) -> dict[str, Any]:
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except ImportError as exc:
        raise CarlaGenerationError("OpenCV and NumPy are required for CARLA generation") from exc

    rng = random.Random(seed)
    scenario_id = f"carla-{seed:08d}-{scenario_index:04d}-{kind}"
    scenario_dir = output_root / "scenarios" / scenario_id
    scenario_dir.mkdir(parents=True, exist_ok=True)
    video_path = scenario_dir / "primary_camera.mp4"
    masks_path = scenario_dir / "instance_masks.npz"
    metadata_path = scenario_dir / "scenario.json"
    actors: list[Any] = []
    sensors: list[Any] = []
    collision_events: list[dict[str, Any]] = []
    rgb_queue: queue.Queue[Any] = queue.Queue()
    instance_queue: queue.Queue[Any] = queue.Queue()
    packed_ego_masks: list[Any] = []
    packed_target_masks: list[Any] = []
    mask_track: list[dict[str, Any]] = []
    instance_encoding_counts: dict[str, int] = {}
    writer = None
    scenario_failed = False

    all_spawn_points = list(enumerate(world.get_map().get_spawn_points()))
    map_name = str(world.get_map().name)
    if "Town05" in map_name:
        validated = set(VALIDATED_TOWN05_SPAWN_INDICES)
        spawn_points = [
            item for item in all_spawn_points if item[0] in validated
        ]
    else:
        spawn_points = all_spawn_points
    rng.shuffle(spawn_points)
    if not spawn_points:
        raise CarlaGenerationError("CARLA map has no vehicle spawn points")
    ego = target = None
    geometry = None
    ego_spawn_index = None
    try:
        ego_blueprint, target_blueprint = choose_vehicle_blueprints(world, rng)
        for spawn_index, ego_transform in spawn_points[:80]:
            candidate_ego = world.try_spawn_actor(ego_blueprint, ego_transform)
            if candidate_ego is None:
                continue
            candidate_geometry = geometry_for_scenario(carla, kind, ego_transform, rng)
            candidate_target = world.try_spawn_actor(
                target_blueprint,
                candidate_geometry.target_transform,
            )
            if candidate_target is None:
                candidate_ego.destroy()
                continue
            ego, target, geometry = candidate_ego, candidate_target, candidate_geometry
            ego_spawn_index = int(spawn_index)
            break
        if ego is None or target is None or geometry is None:
            raise CarlaGenerationError(f"Could not place actors for {kind}")
        actors.extend((ego, target))
        print(
            f"CARLA {scenario_id}: spawn={ego_spawn_index} "
            f"ego={ego.type_id} target={target.type_id}"
        )
        ego.apply_control(carla.VehicleControl(brake=1.0, hand_brake=True))
        target.apply_control(carla.VehicleControl(brake=1.0, hand_brake=True))

        library = world.get_blueprint_library()
        rgb_blueprint = library.find("sensor.camera.rgb")
        rgb_blueprint.set_attribute("image_size_x", str(WIDTH))
        rgb_blueprint.set_attribute("image_size_y", str(HEIGHT))
        rgb_blueprint.set_attribute("fov", "105")
        rgb_blueprint.set_attribute("sensor_tick", "0.0")
        rgb_sensor = world.spawn_actor(
            rgb_blueprint,
            camera_transform(carla, geometry.camera),
            attach_to=ego,
        )
        instance_blueprint = library.find("sensor.camera.instance_segmentation")
        instance_blueprint.set_attribute("image_size_x", str(WIDTH))
        instance_blueprint.set_attribute("image_size_y", str(HEIGHT))
        instance_blueprint.set_attribute("fov", "105")
        instance_blueprint.set_attribute("sensor_tick", "0.0")
        instance_sensor = world.spawn_actor(
            instance_blueprint,
            camera_transform(carla, geometry.camera),
            attach_to=ego,
        )
        collision_sensor = world.spawn_actor(
            library.find("sensor.other.collision"),
            carla.Transform(),
            attach_to=ego,
        )
        sensors.extend((rgb_sensor, instance_sensor, collision_sensor))
        rgb_sensor.listen(rgb_queue.put)
        instance_sensor.listen(instance_queue.put)

        scenario_start: list[float | None] = [None]

        def collision_callback(event: Any) -> None:
            timestamp = float(event.timestamp)
            origin = scenario_start[0]
            impulse = event.normal_impulse
            collision_events.append(
                {
                    "frame": int(event.frame),
                    "time_sec": round(timestamp - origin, 4) if origin is not None else None,
                    "other_actor_id": int(event.other_actor.id),
                    "other_actor_type": str(event.other_actor.type_id),
                    "normal_impulse": {
                        "x": round(float(impulse.x), 5),
                        "y": round(float(impulse.y), 5),
                        "z": round(float(impulse.z), 5),
                        "magnitude": round(vector_length(impulse), 5),
                    },
                }
            )

        collision_sensor.listen(collision_callback)
        writer = cv2.VideoWriter(
            str(video_path),
            cv2.VideoWriter_fourcc(*"mp4v"),
            FPS,
            (WIDTH, HEIGHT),
        )
        if not writer.isOpened():
            raise CarlaGenerationError(f"Could not create synthetic MP4: {video_path}")

        warmup_frames = 5
        for _ in range(warmup_frames):
            frame = world.tick()
            get_sensor_frame(rgb_queue, frame, "RGB")
            get_sensor_frame(instance_queue, frame, "instance segmentation")
        scenario_start[0] = float(world.get_snapshot().timestamp.elapsed_seconds)
        frames = int(round(SCENARIO_DURATION_SEC * FPS))
        door_opened = False
        for frame_number in range(frames):
            elapsed = frame_number / FPS
            if elapsed >= ACTION_START_SEC:
                if geometry.neighbor_door:
                    target.apply_control(carla.VehicleControl(brake=1.0, hand_brake=True))
                    if not door_opened:
                        try:
                            door = carla.VehicleDoor.FL if geometry.camera == "right_repeater" else carla.VehicleDoor.FR
                            target.open_door(door)
                        except (AttributeError, RuntimeError):
                            target.open_door(carla.VehicleDoor.All)
                        door_opened = True
                else:
                    target.apply_control(carla.VehicleControl(brake=0.0, hand_brake=False))
                    target.set_target_velocity(geometry.velocity)
            frame = world.tick()
            image = get_sensor_frame(rgb_queue, frame, "RGB")
            instance_image = get_sensor_frame(
                instance_queue,
                frame,
                "instance segmentation",
            )
            writer.write(convert_image(image))
            ego_mask, target_mask, encoding = decode_actor_masks(
                instance_image,
                int(ego.id),
                int(target.id),
            )
            instance_encoding_counts[encoding] = instance_encoding_counts.get(encoding, 0) + 1
            packed_ego_masks.append(np.packbits(ego_mask.reshape(-1)))
            packed_target_masks.append(np.packbits(target_mask.reshape(-1)))
            mask_track.append(
                {
                    "frame": int(frame),
                    "time_sec": round(elapsed, 4),
                    **mask_geometry(ego_mask, target_mask),
                }
            )
        writer.release()
        writer = None
        np.savez_compressed(
            masks_path,
            ego_masks=np.stack(packed_ego_masks),
            target_masks=np.stack(packed_target_masks),
            frames=np.asarray([item["frame"] for item in mask_track], dtype=np.int64),
            times=np.asarray([item["time_sec"] for item in mask_track], dtype=np.float32),
            mask_height=np.asarray([HEIGHT], dtype=np.int32),
            mask_width=np.asarray([WIDTH], dtype=np.int32),
            bit_order=np.asarray(["big"]),
        )
        first_collision = min(
            (
                item
                for item in collision_events
                if item.get("time_sec") is not None and float(item["time_sec"]) >= 0.0
            ),
            key=lambda item: float(item["time_sec"]),
            default=None,
        )
        actual_positive = first_collision is not None
        intended_positive = kind in POSITIVE_SCENARIOS
        record = {
            "schema_version": SCHEMA_VERSION,
            "generator_version": GENERATOR_VERSION,
            "scenario_id": scenario_id,
            "created_at": utc_now(),
            "simulator": {
                "name": "CARLA",
                "version": CARLA_VERSION,
                "map": str(world.get_map().name),
                "fixed_delta_seconds": 1.0 / FPS,
            },
            "source_role": "synthetic_auxiliary",
            "promotion_eligible": False,
            "real_world_contact_ground_truth": False,
            "simulated_collision_ground_truth": True,
            "scenario_kind": kind,
            "scenario_seed": seed,
            "split": split_for_scenario(scenario_id),
            "camera": geometry.camera,
            "duration_sec": SCENARIO_DURATION_SEC,
            "fps": FPS,
            "width": WIDTH,
            "height": HEIGHT,
            "action_start_sec": ACTION_START_SEC,
            "intended_positive": intended_positive,
            "actual_collision": actual_positive,
            "contact_outcome": "impact" if actual_positive else "no_contact",
            "synthetic_target_severity": "IMPORTANT" if actual_positive else "IGNORE",
            "impact_time_sec": float(first_collision["time_sec"]) if first_collision else None,
            "target_speed_mps": round(geometry.speed_mps, 4),
            "ego_spawn_index": ego_spawn_index,
            "spawn_pool": (
                "validated_town05_all_stable_scenarios_v2"
                if "Town05" in str(world.get_map().name)
                else "unvalidated_map_spawn_points"
            ),
            "ego_spawn_transform": {
                "x": round(float(ego.get_transform().location.x), 5),
                "y": round(float(ego.get_transform().location.y), 5),
                "z": round(float(ego.get_transform().location.z), 5),
                "yaw": round(float(ego.get_transform().rotation.yaw), 5),
            },
            "ego_actor": {"id": int(ego.id), "type": str(ego.type_id)},
            "target_actor": {"id": int(target.id), "type": str(target.type_id)},
            "collision_events": collision_events,
            "instance_segmentation": {
                "path": str(masks_path.resolve()),
                "sha256": sha256_file(masks_path),
                "bytes": masks_path.stat().st_size,
                "encoding_counts": instance_encoding_counts,
                "packed_boolean_masks": True,
                "bit_order": "big",
            },
            "mask_track": mask_track,
            "media": [
                {
                    "path": str(video_path.resolve()),
                    "sha256": sha256_file(video_path),
                    "bytes": video_path.stat().st_size,
                    "camera": geometry.camera,
                }
            ],
            "label_provenance": (
                "CARLA collision sensor frame and normal impulse"
                if actual_positive
                else "CARLA scenario completed without an ego collision sensor event"
            ),
            "limitations": [
                "Synthetic rendering and physics differ from real Tesla Sentry footage.",
                "This item cannot establish real-world model performance.",
            ],
        }
        write_json(metadata_path, record)
        return record
    except BaseException:
        scenario_failed = True
        raise
    finally:
        if writer is not None:
            writer.release()
        server_alive = (
            not scenario_failed
            and (server_process is None or server_process.poll() is None)
        )
        for sensor in sensors if server_alive else ():
            try:
                sensor.stop()
            except (AttributeError, RuntimeError):
                pass
        for actor in reversed([*sensors, *actors]) if server_alive else ():
            try:
                actor.destroy()
            except RuntimeError:
                pass
        if server_alive:
            try:
                world.tick()
            except RuntimeError:
                pass


def build_training_manifest(output_root: Path, records: list[dict[str, Any]]) -> dict[str, Any]:
    items = []
    for record in records:
        media = [str(item["path"]) for item in record.get("media", []) if item.get("path")]
        objects: list[dict[str, Any]] = []
        for point in record.get("mask_track", []):
            if not isinstance(point, dict):
                continue
            time_sec = point.get("time_sec")
            ego_box = point.get("ego_bbox_xyxy")
            target_box = point.get("target_bbox_xyxy")
            if isinstance(ego_box, list) and len(ego_box) == 4:
                objects.append(
                    {
                        "time_sec": time_sec,
                        "class_name": "ego_vehicle",
                        "track_id": "ego",
                        "bbox_xyxy": ego_box,
                        "source": "carla_instance_segmentation",
                    }
                )
            if isinstance(target_box, list) and len(target_box) == 4:
                mask_distance = point.get("mask_distance_pixels")
                normalized_distance = (
                    min(
                        1.0,
                        max(0.0, float(mask_distance))
                        / math.hypot(
                            float(record.get("width") or WIDTH),
                            float(record.get("height") or HEIGHT),
                        ),
                    )
                    if mask_distance is not None
                    else None
                )
                objects.append(
                    {
                        "time_sec": time_sec,
                        "class_name": "vehicle",
                        "track_id": "target",
                        "bbox_xyxy": target_box,
                        "source": "carla_instance_segmentation",
                        "pair_geometry_source": "carla_instance_actor_masks",
                        "pair_mask_adjacency": bool(point.get("masks_adjacent")),
                        "pair_mask_distance_norm": normalized_distance,
                    }
                )
        items.append(
            {
                "incident_id": record["scenario_id"],
                "source_group_hash": hashlib.sha256(
                    str(record["scenario_id"]).encode("utf-8")
                ).hexdigest(),
                "split": record["split"],
                "media": media,
                "duration_sec": record["duration_sec"],
                "contact_outcome": record["contact_outcome"],
                "impact_time_sec": record["impact_time_sec"],
                "external_event_time_sec": record["impact_time_sec"],
                "external_event_target": 1 if record["actual_collision"] else 0,
                "category": record["scenario_kind"],
                "geometry_provenance": "carla_instance_segmentation",
                "objects": objects,
                "simulation_mask_path": record.get("instance_segmentation", {}).get("path"),
                "source_role": "synthetic_auxiliary",
                "promotion_eligible": False,
            }
        )
    split_counts: dict[str, int] = {}
    positive_count = 0
    negative_count = 0
    for item in items:
        split_counts[item["split"]] = split_counts.get(item["split"], 0) + 1
        if item["external_event_target"]:
            positive_count += 1
        else:
            negative_count += 1
    manifest = {
        "schema_version": "mimir_training_manifest_v1",
        "created_at": utc_now(),
        "training_purpose": "synthetic_stationary_collision_timing_pretraining_only",
        "source_role": "synthetic_auxiliary",
        "promotion_eligible": False,
        "real_world_evaluation_eligible": False,
        "split_assignment_version": SPLIT_VERSION,
        "source_audit": {
            "complete_items": len(items),
            "positive_items": positive_count,
            "hard_negative_items": negative_count,
            "complete_split_counts": split_counts,
            "simulated_collision_ground_truth": True,
            "real_world_contact_ground_truth": False,
        },
        "temporal_items": items,
    }
    write_json(output_root / "training_manifest.json", manifest)
    return manifest


def terminate_server(process: subprocess.Popen[Any]) -> None:
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
    else:
        process.terminate()
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def generate(args: argparse.Namespace) -> int:
    carla_root = Path(args.carla_root).resolve()
    output_root = Path(args.output).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    carla = import_carla(carla_root)
    process = None
    world = None
    original_settings = None
    records: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    new_completed = 0
    scenario_kinds = tuple(
        value.strip()
        for value in str(args.scenario_kinds or "").split(",")
        if value.strip()
    ) or STABLE_PILOT_SCENARIOS
    invalid_kinds = sorted(set(scenario_kinds) - set(ALL_SCENARIOS))
    if invalid_kinds:
        raise CarlaGenerationError(
            f"Unsupported --scenario-kinds values: {', '.join(invalid_kinds)}"
        )
    try:
        if args.launch_server:
            process = launch_server(carla_root, args.port, output_root / "carla_server.log")
        client = wait_for_server(carla, args.port, args.server_timeout_sec)
        client.set_timeout(max(120.0, float(args.server_timeout_sec)))
        world = client.load_world(args.map)
        client.set_timeout(60.0)
        original_settings = world.get_settings()
        settings = world.get_settings()
        settings.synchronous_mode = True
        settings.fixed_delta_seconds = 1.0 / FPS
        settings.substepping = True
        settings.max_substep_delta_time = 0.01
        settings.max_substeps = 10
        world.apply_settings(settings)
        existing = {
            str(item.get("scenario_id")): item
            for path in (output_root / "scenarios").glob("*/scenario.json")
            for item in [json.loads(path.read_text(encoding="utf-8"))]
        }
        records.extend(existing.values())
        for index in range(args.scenarios):
            kind = scenario_kinds[index % len(scenario_kinds)]
            seed = args.seed + index
            scenario_id = f"carla-{seed:08d}-{index:04d}-{kind}"
            if scenario_id in existing:
                print(f"CARLA scenario {index + 1}/{args.scenarios}: cached {kind}")
                continue
            try:
                record = run_scenario(
                    carla,
                    world,
                    output_root,
                    index,
                    kind,
                    seed,
                    server_process=process,
                )
                records.append(record)
                existing[scenario_id] = record
                new_completed += 1
                result = "collision" if record["actual_collision"] else "no collision"
                print(
                    f"CARLA scenario {index + 1}/{args.scenarios}: "
                    f"{kind} -> {result}"
                )
            except (OSError, RuntimeError, CarlaGenerationError, queue.Empty) as exc:
                failures.append(
                    {
                        "scenario_index": index,
                        "scenario_kind": kind,
                        "seed": seed,
                        "error": f"{type(exc).__name__}: {exc}",
                        "batch_aborted": True,
                        "reason": "scenario failure requires a clean server restart",
                    }
                )
                print(f"CARLA scenario {index + 1}/{args.scenarios}: FAILED {exc}")
                break
        for record in records:
            record["split"] = split_for_scenario(str(record.get("scenario_id") or ""))
            write_json(
                output_root
                / "scenarios"
                / str(record.get("scenario_id") or "")
                / "scenario.json",
                record,
            )
        records.sort(key=lambda item: str(item.get("scenario_id") or ""))
        manifest = build_training_manifest(output_root, records)
        generation_report = {
            "schema_version": "mimir_carla_generation_report_v1",
            "generated_at": utc_now(),
            "generator_version": GENERATOR_VERSION,
            "carla_version": CARLA_VERSION,
            "map": args.map,
            "requested_scenarios": args.scenarios,
            "completed_scenarios": len(records),
            "new_completed_scenarios": new_completed,
            "failed_scenarios": len(failures),
            "actual_collisions": sum(1 for item in records if item.get("actual_collision")),
            "no_collision": sum(1 for item in records if not item.get("actual_collision")),
            "intended_label_mismatches": sum(
                1
                for item in records
                if bool(item.get("intended_positive")) != bool(item.get("actual_collision"))
            ),
            "training_manifest": str((output_root / "training_manifest.json").resolve()),
            "training_manifest_sha256": sha256_file(output_root / "training_manifest.json"),
            "source_role": "synthetic_auxiliary",
            "promotion_eligible": False,
            "scenario_kinds": list(scenario_kinds),
            "excluded_unstable_scenarios": sorted(
                set(ALL_SCENARIOS) - set(scenario_kinds)
            ),
            "failures": failures,
            "source_audit": manifest["source_audit"],
        }
        write_json(output_root / "generation_report.json", generation_report)
    finally:
        if (
            world is not None
            and original_settings is not None
            and (process is None or process.poll() is None)
        ):
            try:
                world.apply_settings(original_settings)
            except RuntimeError:
                pass
        if process is not None:
            terminate_server(process)
            log_handle = getattr(process, "_mimir_log_handle", None)
            if log_handle is not None:
                log_handle.close()
    print(f"CARLA synthetic output: {output_root}")
    return 0 if records and not failures else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--carla-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--scenarios", type=int, default=60)
    parser.add_argument("--seed", type=int, default=20260723)
    parser.add_argument("--map", default="Town05")
    parser.add_argument("--port", type=int, default=2000)
    parser.add_argument("--server-timeout-sec", type=float, default=180.0)
    parser.add_argument(
        "--scenario-kinds",
        default="",
        help=(
            "Comma-separated scenario families. The default stable pilot uses "
            "rear/front impacts and aligned rear near misses."
        ),
    )
    parser.add_argument("--launch-server", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return generate(args)
    except (CarlaGenerationError, OSError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"CARLA generation error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
