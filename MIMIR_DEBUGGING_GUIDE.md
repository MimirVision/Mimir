# Mimir Debugging Guide

This guide gives practical first steps when something looks wrong.

## First Commands To Run

From the backend folder:

```powershell
cd C:\Mimir_Backend
python -m py_compile tesla_ai_sorter.py
python validate_mimir_output.py
python inspect_latest_session.py
```

For a fresh scan:

```powershell
python tesla_ai_sorter.py --input "C:\mimir\test" --mode balanced
python validate_mimir_output.py
```

For performance:

```powershell
python benchmark_large_scan.py --input "C:\mimir\test" --mode balanced --max-runtime-min 60
```

## If A Scan Is Slow

Check:

```text
C:\Mimir_Backend\MimirOutput\performance_report.json
C:\Mimir_Backend\MimirOutput\performance_report.csv
C:\Mimir_Backend\MimirOutput\large_scan_benchmark.json
```

Look for:

- `total_runtime_sec`
- `stage_timings`
- `slowest_groups`
- `prepass_groups_processed`
- `deep_analysis_groups`
- `skipped_low_interest_groups`
- `ai_calls`
- `ai_review_runtime_sec`

Common causes:

- AI model is running on too many groups.
- YOLO is scanning too many frames.
- Prepass thresholds are too low.
- Large clips are not being skipped as low-interest.

Try:

```powershell
python tesla_ai_sorter.py --input "C:\mimir\test" --mode fast
python tesla_ai_sorter.py --input "C:\mimir\test" --mode balanced --ai-review-budget 10
```

## If A Crash Is Ignored

Open `latest_session.json` and search for the clip filename.

Check:

- Was an incident created?
- `final_severity`
- `impact_level`
- `impact_score`
- `possible_impact`
- `crash_safety_triggered`
- `severity_reasons`
- `classification_debug`
- `timeline_markers`

If the group was skipped, check:

- `prepass_motion_score`
- `prepass_candidate_reason`
- `deep_analysis_performed`
- `skipped_reason`

Possible next steps:

- Lower `prepass_deep_threshold`.
- Lower crash/motion thresholds slightly.
- Increase sample rate by using `quality` mode.
- Confirm the relevant camera angle is in `camera_clips`.

Do not remove crash safety floors. Real impacts should not be downgraded to Ignore.

## If Normal Traffic Is Flagged

Check:

- `normal_passing_traffic_evidence`
- `brief_vehicle_only`
- `object_persistence_summary`
- `impact_level`
- `contact_level`
- `possible_contact`
- `possible_impact`
- `ai_evidence_review`
- `classification_debug.ai_blocked_reason`

If AI caused the flag, inspect:

```text
C:\Mimir_Backend\MimirOutput\AIAudit\<incident_id>\
```

Open:

- `ai_prompt.txt`
- `ai_raw_response.txt`
- `ai_parsed_response.json`
- `local_evidence.json`
- `final_decision.json`
- `ai_review_image.jpg`

Possible next steps:

- Raise contact/impact thresholds slightly.
- Make prepass less eager.
- Reduce AI budget during speed tests.
- Add feedback from the frontend so future training can learn from it.

## If Video Paths Break

Check these incident fields:

- `video_path`
- `library_video_path`
- `source_video`
- `original_source_video`
- `camera_clips`
- `video_exists`
- `storage_state`
- `trash_video_path`

The frontend tries safe video paths in this general order:

1. `video_path`
2. `library_video_path`
3. `source_video`
4. `original_source_video`
5. first camera clip path

If a file moved after review, make sure storage actions updated:

- `library_video_path`
- `trash_video_path`
- `camera_clips[].library_path`
- `camera_clips[].trash_path`
- `storage_state`

Use the Files drawer in the desktop app to inspect current location.

## If The App Crashes When Opening An Incident

Backend things to check:

- Does `latest_session.json` contain malformed incident fields?
- Is `timeline_markers` a list?
- Are image/video paths strings?
- Are missing files marked with `video_exists: false`?

Frontend crash logs are saved to:

```text
%USERPROFILE%\Documents\Mimir Logs\app_crash_log.txt
```

## If AI Looks Wrong

Look at AI audit files:

```text
C:\Mimir_Backend\MimirOutput\AIAudit\<incident_id>\
```

Useful files:

- `ai_prompt.txt`: what Mimir asked the model.
- `ai_raw_response.txt`: exact model response.
- `ai_parsed_response.json`: parsed structured response.
- `local_evidence.json`: local signals Mimir computed.
- `final_decision.json`: why final severity was chosen.
- `ai_review_image.jpg`: image/contact sheet sent to AI.

Remember: AI is supporting evidence. Local rules are the safety floor.

## Where Reports And Logs Are Saved

Backend:

```text
C:\Mimir_Backend\MimirOutput\latest_session.json
C:\Mimir_Backend\MimirOutput\performance_report.json
C:\Mimir_Backend\MimirOutput\performance_report.csv
C:\Mimir_Backend\MimirOutput\large_scan_benchmark.json
C:\Mimir_Backend\MimirOutput\AIAudit\<incident_id>\
C:\Mimir_Backend\MimirOutput\incidents\<incident_id>\
```

Frontend/user logs:

```text
%USERPROFILE%\Documents\Mimir Logs\app_crash_log.txt
%USERPROFILE%\Documents\Mimir Feedback\
```

Training export:

```text
C:\MimirTrainingDataset\
```

## Safe Debugging Habit

When tuning, use this loop:

```powershell
python -m py_compile tesla_ai_sorter.py
python tesla_ai_sorter.py --input "C:\mimir\test" --mode balanced
python validate_mimir_output.py
python inspect_latest_session.py
python benchmark_large_scan.py --input "C:\mimir\test" --mode balanced
```

Make one small change at a time, then compare outputs.

