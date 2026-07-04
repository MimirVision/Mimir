# Mimir Backend Guide

This guide explains the Python backend in beginner-friendly terms. The main scanner is `tesla_ai_sorter.py`. It reads a folder of vehicle clips, finds moments worth reviewing, creates evidence images, and writes `MimirOutput\latest_session.json` for the desktop app.

## Main Files

- `tesla_ai_sorter.py`: the scanner. It discovers videos, groups camera angles, analyzes motion/objects, optionally asks a local vision model for evidence, decides severity, and writes output JSON.
- `config.py`: base folders and model paths.
- `discover_footage_source.py`: helper for finding TeslaCam/generic footage sources.
- `mimir_clip_actions.py`: post-review storage actions, such as moving reviewed clips to Mimir Library or Mimir Trash.
- `validate_mimir_output.py`: checks that the latest output is readable and shaped correctly.
- `inspect_latest_session.py`: prints a readable summary of the latest session.
- `benchmark_large_scan.py`: runs a scan and prints performance readiness numbers.
- `export_training_dataset.py`: exports labeled incidents for future model training.

## Scan Flow

Typical command:

```powershell
cd C:\Mimir_Backend
python tesla_ai_sorter.py --input "C:\mimir\test" --mode balanced
```

High-level flow:

1. Validate the input folder.
2. Discover footage sources and MP4 files.
3. Group Tesla-style multi-camera clips into event groups.
4. Run a fast prepass for each group.
5. Deep-scan only groups worth reviewing.
6. Generate thumbnails, contact sheets, and timeline markers.
7. Run optional AI review within the AI budget.
8. Resolve final severity.
9. Write incident files, performance reports, and `latest_session.json`.

Scanning is review-only by default. It does not move or delete source clips during scan.

## Source Discovery

Source discovery answers: “What did the user give us?”

The scanner supports:

- A TeslaCam drive/folder.
- `SentryClips`, `SavedClips`, and `RecentClips`.
- A generic folder containing `.mp4` files.

Important output fields:

- `selected_input`
- `detected_source_type`
- `scan_roots`
- `source_categories_found`
- `event_groups_found`
- `source_report`

If a folder has no footage, Mimir writes a friendly session result instead of crashing.

## Camera Grouping

Tesla-style filenames look like:

```text
YYYY-MM-DD_HH-MM-SS-front.mp4
YYYY-MM-DD_HH-MM-SS-back.mp4
YYYY-MM-DD_HH-MM-SS-left_repeater.mp4
YYYY-MM-DD_HH-MM-SS-right_repeater.mp4
```

Mimir groups clips by timestamp and event folder. One group can include several camera angles. Grouped incidents include:

- `event_group_id`
- `camera_clips`
- `available_cameras`
- `primary_camera`
- `camera_count`

The UI still uses `video_path`, so the backend keeps `video_path` pointing at the selected primary camera.

## Prepass

The prepass is a cheap first look. It samples a low number of frames from each camera and estimates:

- motion score
- scene change
- possible impact spikes
- mostly static clips
- candidate windows
- primary camera candidate

Prepass output fields include:

- `prepass_motion_score`
- `prepass_candidate_reason`
- `deep_analysis_performed`
- `skipped_reason`

Boring/static groups should be skipped or handled cheaply.

## Deep Analysis

Deep analysis runs on groups that look interesting enough. It samples more frames, runs YOLO detections, measures motion/contact/impact evidence, and creates incident output.

Deep analysis focuses on candidate windows instead of every frame. This is important for large scans.

## AI Review

AI review is optional and local. It uses a local vision-language model through Ollama when requested:

```powershell
python tesla_ai_sorter.py --input "C:\mimir\test" --mode balanced --vlm qwen2.5vl:7b
```

AI review is group-based and budgeted. Defaults:

- `fast`: 20 groups
- `balanced`: 50 groups
- `quality` / `thorough`: 150 groups

Override with:

```powershell
python tesla_ai_sorter.py --input "C:\mimir\test" --mode balanced --ai-review-budget 25
```

The AI returns structured evidence. It is not the final decision maker. Local evidence remains primary.

AI audit files are saved under:

```text
C:\Mimir_Backend\MimirOutput\AIAudit\<incident_id>\
```

## Severity Resolver

The resolver combines local evidence and AI evidence.

Local evidence includes:

- motion score
- impact score
- contact score
- crash safety trigger
- person/vehicle detections
- timeline markers
- camera angle evidence

AI can help escalate suspicious cases, but it cannot downgrade protected signals such as strong crash/contact/impact evidence to Ignore.

Useful fields:

- `severity`
- `final_severity`
- `severity_reasons`
- `final_decision_source`
- `classification_debug`

## Thumbnails

Each incident may include:

- `start_frame_image`
- `best_frame_image`
- `end_frame_image`
- `hero_thumbnail`
- `thumbnail`
- `contact_sheet`

Files are usually stored in:

```text
C:\Mimir_Backend\MimirOutput\incidents\<incident_id>\
```

## Timeline Markers

Timeline markers tell the UI where important moments happened in the clip. Field names should stay stable.

Typical marker fields:

- `time_sec`
- `frame_index`
- `type`
- `severity`
- `label`
- `description`

## Performance Reports

The scanner writes:

```text
C:\Mimir_Backend\MimirOutput\performance_report.json
C:\Mimir_Backend\MimirOutput\performance_report.csv
```

These show:

- total runtime
- stage timings
- prepass/deep scan counts
- AI calls and AI runtime
- sampled frames
- slowest groups

Use the benchmark wrapper for large sets:

```powershell
python benchmark_large_scan.py --input "D:\TeslaCam" --mode balanced --max-runtime-min 60
```

## Cache

Some reports include cache fields such as `cache_hits` and `cache_misses` when available. If cache fields are missing, tools should treat them as zero or “not reported.”

Cache-like behavior may be added or expanded over time, so code should read these fields defensively.

## Clip Actions

`mimir_clip_actions.py` is for post-review storage changes. It is not used during scanning.

Examples:

```powershell
python mimir_clip_actions.py --session "C:\Mimir_Backend\MimirOutput\latest_session.json" --incident-id incident_0001 --move-to-library
python mimir_clip_actions.py --session "C:\Mimir_Backend\MimirOutput\latest_session.json" --incident-id incident_0001 --delete
python mimir_clip_actions.py --session "C:\Mimir_Backend\MimirOutput\latest_session.json" --move-status IMPORTANT --move-to-library
python mimir_clip_actions.py --session "C:\Mimir_Backend\MimirOutput\latest_session.json" --cleanup-reviewed
```

These actions move files only after review. They should never permanently delete clips.

## Output Locations

Main outputs:

```text
C:\Mimir_Backend\MimirOutput\latest_session.json
C:\Mimir_Backend\MimirOutput\performance_report.json
C:\Mimir_Backend\MimirOutput\performance_report.csv
C:\Mimir_Backend\MimirOutput\incidents\<incident_id>\
C:\Mimir_Backend\MimirOutput\AIAudit\<incident_id>\
```

Optional user-facing storage:

```text
%USERPROFILE%\Videos\Mimir Library\
%USERPROFILE%\Videos\Mimir Library\_Mimir Trash\
```

