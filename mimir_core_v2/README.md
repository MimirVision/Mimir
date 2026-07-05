# Mimir Core v2

Mimir Core v2 is a clean, event-based backend prototype that lives beside the existing scanner.
It does not replace or modify `tesla_ai_sorter.py`.

## Core Rule

A camera file is not an incident.
A timestamp/event folder is an incident.

For Tesla-style clips such as:

```text
2026-04-19_12-43-26-front.mp4
2026-04-19_12-43-26-back.mp4
2026-04-19_12-43-26-left_repeater.mp4
2026-04-19_12-43-26-right_repeater.mp4
```

Core v2 creates one event group and one incident with four camera clips attached.

## Flow

1. `source_discovery.py` finds MP4 files without moving or modifying them.
2. `event_grouping.py` groups Tesla camera angles by event folder and timestamp.
3. `frame_sampler.py` samples a few frames if OpenCV is available.
4. `evidence_extractor.py` creates a simple local evidence summary.
5. `ai_reviewer.py` provides a safe optional AI-review hook. In v2.0.1 it is stubbed.
6. `severity_resolver.py` chooses a simple initial severity and primary camera.
7. `output_writer.py` writes `MimirOutputV2/latest_session.json`.
8. `cli.py` ties the pieces together.

## Run

```powershell
python mimir_core_v2_scan.py --input "C:\mimir\test" --mode balanced
```

Optional model argument:

```powershell
python mimir_core_v2_scan.py --input "C:\mimir\test" --mode balanced --vlm qwen2.5vl:7b
```

The first implementation focuses on clean structure and correct grouping, not perfect detection.

## Output

Output is written to:

```text
C:\Mimir_Backend\MimirOutputV2\latest_session.json
```

The session includes:

- `schema_version: "mimir_v2"`
- `scanner_version: "mimir_core_v2_0_1"`
- `selected_input`
- `event_groups_found`
- `incidents`
- `warnings`

Each incident includes the grouped camera fields expected by the frontend:

- `event_group_id`
- `event_timestamp`
- `camera_count`
- `available_cameras`
- `primary_camera`
- `camera_clips`
- `video_path`

