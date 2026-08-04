# Mimir Core v2

Mimir Core v2 is the shipped, event-based backend. The earlier `tesla_ai_sorter.py` scanner has
been removed — Core v2 is not a prototype living beside it anymore.

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
3. `frame_sampler.py` samples frames per event group according to scan mode.
4. `evidence_extractor.py` extracts local evidence: motion (`motion_analysis.py`), object
   detection (`onnx_object_detector.py`, RF-DETR Nano via ONNX Runtime), and contact/impact
   heuristics with multi-camera corroboration.
5. `key_moment_refiner.py` runs a dense second pass to pinpoint contact timing.
6. `severity_resolver.py` resolves final severity with hard floors/caps that AI cannot override.
7. `ai_reviewer.py` / `ai_enrichment.py` optionally add a local Ollama VLM second opinion — this
   can only escalate a REVIEW case, never downgrade protected evidence.
8. `thumbnailer.py` generates thumbnails/contact sheets.
9. `output_writer.py` writes `MimirOutputV2/latest_session.json` and the per-session archive.
10. `cli.py` ties the pieces together and reports progress via `progress.py`.

See `MIMIR_BACKEND_GUIDE.md` (one directory up) for the full architecture writeup.

## Run

```powershell
python mimir_core_v2_scan.py --input "C:\mimir\test" --mode balanced
```

Optional model argument:

```powershell
python mimir_core_v2_scan.py --input "C:\mimir\test" --mode balanced --vlm qwen2.5vl:7b
```

## Output

Output is written to:

```text
C:\MimirDev\backend\MimirOutputV2\latest_session.json
```

The session includes `schema_version`, `scanner_version`, `selected_input`, `event_groups_found`,
`incidents`, `warnings`, and a `performance` block.

Each incident includes the grouped camera fields expected by the frontend: `event_group_id`,
`event_timestamp`, `camera_count`, `available_cameras`, `primary_camera`, `camera_clips`,
`video_path`.
