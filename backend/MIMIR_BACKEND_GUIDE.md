# Mimir Backend Guide

This guide explains the Python backend in beginner-friendly terms. The shipped
scanner is `mimir_core_v2` (Core v2). It reads a folder of vehicle clips, finds
moments worth reviewing, creates evidence images, and writes
`MimirOutputV2\latest_session.json` for the desktop app.

`tesla_ai_sorter.py` (the earlier v1 scanner) has been removed. It is no longer
built, run, or referenced by the shipped app — Core v2 replaced it entirely.

## Main Files

- `mimir_core_v2_scan.py`: thin entry point that calls `mimir_core_v2.cli.main()`.
- `mimir_core_v2/cli.py`: orchestrates the scan (`run_scan`) and AI enrichment pass.
- `mimir_core_v2/source_discovery.py`: finds TeslaCam/generic footage sources (`discover_videos`).
- `mimir_core_v2/event_grouping.py`: groups multi-camera clips into event groups (`group_videos`).
- `mimir_core_v2/frame_sampler.py`: samples frames per event group (`sample_event_group`).
- `mimir_core_v2/evidence_extractor.py`: the core evidence logic (`extract_evidence`) — motion,
  object detection, contact/impact heuristics, multi-camera corroboration.
- `mimir_core_v2/motion_analysis.py`: frame-diff/optical-flow motion, ego-zone motion, camera shake.
- `mimir_core_v2/onnx_object_detector.py`: RF-DETR Nano person/vehicle detection via ONNX Runtime.
- `mimir_core_v2/key_moment_refiner.py`: dense second-pass timing refinement (`refine_key_moment`).
- `mimir_core_v2/severity_resolver.py`: rule-based IGNORE/REVIEW/IMPORTANT decision (`resolve_severity`),
  with hard floors and caps that AI review cannot override.
- `mimir_core_v2/ai_enrichment.py` / `ai_reviewer.py`: optional local Ollama VLM second opinion. Can only
  escalate REVIEW, never downgrade hard local evidence (`can_change_final_severity` stays `False`).
- `mimir_core_v2/thumbnailer.py`: generates thumbnails/contact sheets (`generate_thumbnails`).
- `mimir_core_v2/output_writer.py`: writes `latest_session.json` and the per-session archive.
- `mimir_core_v2/detector_cache.py`: SQLite-backed detection/metric cache.
- `mimir_core_v2/model_manifest.py`: checksum-pinned model provenance (see `model_manifest.json`).
- `mimir_core_v2/runtime_paths.py`: resolves the output directory for dev vs. packaged builds.
- `mimir_core_v2/ego_vehicle.py`: per-camera ego-vehicle region masks.
- `mimir_clip_actions.py`: post-review storage actions (move to Mimir Library / Mimir Trash).
- `export_training_dataset.py`: exports consented, labeled incidents for future model training.

## Scan Flow

Typical command:

```powershell
cd C:\MimirDev\backend
.venv\Scripts\python.exe mimir_core_v2_scan.py --input "C:\mimir\test" --mode balanced
```

`--mode` is one of `fast`, `balanced`, `thorough` (there is no `quality` mode in Core v2).

High-level flow (matches the progress stages `mimir_core_v2/cli.py:run_scan` emits):

1. `reading_clips` — discover footage sources and MP4 files (`source_discovery.py`).
2. `reading_event_metadata` — read per-clip event metadata.
3. `grouping_camera_angles` — group Tesla-style multi-camera clips into event groups (`event_grouping.py`).
4. `detecting_activity` — for each event group: sample frames, extract local evidence (motion +
   object detection), refine the key moment, resolve severity, generate thumbnails.
5. `reviewing_suspicious_moments` / `ai_review` — optional local AI second opinion, budgeted per mode.
6. `writing_results` — write `latest_session.json`, the per-session archive, and performance data.

Scanning is review-only by default. It does not move or delete source clips during scan.

## Source Discovery

Source discovery answers: "What did the user give us?" (`mimir_core_v2/source_discovery.py`).

The scanner supports:

- A TeslaCam drive/folder.
- `SentryClips`, `SavedClips`, and `RecentClips`.
- A generic folder containing `.mp4` files.

Important session fields: `selected_input`, `detected_source_type`, `scan_roots`,
`source_categories_found`, `event_groups_found`, `source_report`.

If a folder has no footage, Mimir writes a friendly session result instead of crashing.

## Camera Grouping

Tesla-style filenames look like:

```text
YYYY-MM-DD_HH-MM-SS-front.mp4
YYYY-MM-DD_HH-MM-SS-back.mp4
YYYY-MM-DD_HH-MM-SS-left_repeater.mp4
YYYY-MM-DD_HH-MM-SS-right_repeater.mp4
```

`event_grouping.py` groups clips by timestamp and event folder. One group can include several
camera angles. Grouped incidents include `event_group_id`, `camera_clips`, `available_cameras`,
`primary_camera`, `camera_count`. The UI still uses `video_path`, so the backend keeps
`video_path` pointing at the selected primary camera.

## Evidence Extraction

`evidence_extractor.py` (`extract_evidence`) is the core of the pipeline. It combines:

- Motion signals from `motion_analysis.py` (global motion, localized/contact-zone motion, camera
  shake, ego-vehicle-zone motion).
- Object detections from `onnx_object_detector.py` (RF-DETR Nano, person/vehicle classes only).
- Multi-camera corroboration (the same apparent contact seen from more than one angle).
- Named thresholds such as `MOTION_HIGH_THRESHOLD`, `HARD_CLOSE_OBJECT_AREA_THRESHOLD`,
  `VISUAL_CONTACT_MIN_SCORE`, and `OBJECT_CONTACT_HIGH_SCORE` — see the top of `evidence_extractor.py`
  for the full list and current values.

`key_moment_refiner.py` (`refine_key_moment`) then runs a dense, phase-correlation-stabilized
second pass to pinpoint contact timing more precisely than the sparse first pass.

## AI Review

AI review is optional and local. It uses a local vision-language model through Ollama when requested:

```powershell
.venv\Scripts\python.exe mimir_core_v2_scan.py --input "C:\mimir\test" --mode balanced --vlm qwen2.5vl:7b
```

AI review is group-based and budgeted (`AI_BUDGET_DEFAULTS` in `mimir_core_v2/cli.py`):

- `fast`: 20 groups
- `balanced`: 50 groups
- `thorough`: 150 groups

Override with `--ai-review-budget <n>`.

The AI returns structured evidence stored directly on the incident (`ai_raw_response`,
`ai_evidence`, `ai_evidence_review`, `ai_model`). It is not the final decision maker — see
`ai_enrichment.py`, which hard-codes `can_change_final_severity: False`. Local evidence and the
severity resolver's floors remain the safety authority; AI can only help escalate a REVIEW case,
never downgrade protected IMPORTANT evidence.

## Severity Resolver

`severity_resolver.py` (`resolve_severity`) combines local evidence and AI evidence and applies
hard floors (things that force at least REVIEW or IMPORTANT, e.g. `crash_safety_triggered`,
`impact_level HIGH`, `contact_level HIGH`) and caps. Useful fields: `severity`, `final_severity`,
`severity_reasons`, `severity_floor_applied`, `severity_floor_reason`, `severity_cap_applied`,
`severity_cap_reason`, `classification_debug`.

## Thumbnails

Each incident may include `start_frame_image`, `best_frame_image`, `end_frame_image`,
`hero_thumbnail`, `thumbnail`, `contact_sheet`. Files are stored per-session under:

```text
C:\MimirDev\backend\MimirOutputV2\sessions\<session_id>\thumbnails\
```

## Timeline Markers

Timeline markers tell the UI where important moments happened in the clip. Field names should
stay stable: `time_sec`, `frame_index`, `type`, `severity`, `label`, `description`.

## Output Locations

Main outputs (dev workspace; packaged builds resolve under `%LOCALAPPDATA%\Mimir\MimirOutputV2`,
or wherever `MIMIR_OUTPUT_DIR` points — see `mimir_core_v2/runtime_paths.py`):

```text
C:\MimirDev\backend\MimirOutputV2\latest_session.json
C:\MimirDev\backend\MimirOutputV2\sessions\<session_id>\session.json
C:\MimirDev\backend\MimirOutputV2\sessions\<session_id>\thumbnails\
```

Optional user-facing storage:

```text
%USERPROFILE%\Videos\Mimir Library\
%USERPROFILE%\Videos\Mimir Library\_Mimir Trash\
```

## Clip Actions

`mimir_clip_actions.py` is for post-review storage changes. It is not used during scanning.

```powershell
python mimir_clip_actions.py --session "C:\MimirDev\backend\MimirOutputV2\latest_session.json" --incident-id incident_0001 --move-to-library
python mimir_clip_actions.py --session "C:\MimirDev\backend\MimirOutputV2\latest_session.json" --incident-id incident_0001 --delete
python mimir_clip_actions.py --session "C:\MimirDev\backend\MimirOutputV2\latest_session.json" --move-status IMPORTANT --move-to-library
python mimir_clip_actions.py --session "C:\MimirDev\backend\MimirOutputV2\latest_session.json" --cleanup-reviewed
```

These actions move files only after review. They should never permanently delete clips.

## Release/QA Tooling

The shipped release gate, regression suite, and reliability checks live alongside Core v2, not
as separate v1 scripts:

- `mimir_core_v2_release_check.py` — release readiness gate (`--gate-only` for the strict external gate).
- `mimir_core_v2_regression_suite.py` — regression set runner.
- `mimir_core_v2_reliability.py` — real-fixture reliability gate.
- `mimir_core_v2_verify.py` — quick CI-friendly verification (used by `.github/workflows/core-v2-verify.yml`).

See `docs/RELEASE_READINESS.md` in `C:\MimirDev\desktop` for the full release process.
