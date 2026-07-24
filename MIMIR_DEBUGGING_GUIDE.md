# Mimir Debugging Guide

This guide gives practical first steps when something looks wrong. It targets the shipped
`mimir_core_v2` scanner — the earlier `tesla_ai_sorter.py` scanner has been removed.

## First Commands To Run

From the backend folder:

```powershell
cd C:\Mimir_Backend
.venv\Scripts\python.exe -m py_compile mimir_core_v2_scan.py
.venv\Scripts\python.exe -m unittest discover -s mimir_core_v2 -t . -p "test_*.py"
.venv\Scripts\python.exe inspect_latest_session.py
```

For a fresh scan:

```powershell
.venv\Scripts\python.exe mimir_core_v2_scan.py --input "C:\mimir\test" --mode balanced
```

For a quick pipeline verification (also what CI runs):

```powershell
.venv\Scripts\python.exe mimir_core_v2_verify.py --level quick
```

## If A Scan Is Slow

Check the session's `performance` block in `latest_session.json`, and the fields:

- `performance.total_runtime_sec`
- `performance.parts_sec` / `performance.stage_runtime_sec`
- `performance.object_detector_runtime_sec`, `object_detector_inference_count`
- `performance.detector_cache_hits` / `detector_cache_misses`
- `performance.ai_calls`, `ai_runtime_sec`

Common causes:

- AI model is running on too many groups — lower `--ai-review-budget`.
- Object detection is scanning too many frames — check `frame_sampler.py` sampling rate for the
  chosen `--mode`.
- Large clips are not being skipped as low-interest.

Try:

```powershell
.venv\Scripts\python.exe mimir_core_v2_scan.py --input "C:\mimir\test" --mode fast
.venv\Scripts\python.exe mimir_core_v2_scan.py --input "C:\mimir\test" --mode balanced --ai-review-budget 10
```

## If A Crash Is Ignored

Open `latest_session.json` and search for the clip filename or `incident_id`.

Check:

- Was an incident created at all?
- `final_severity`
- `impact_level`, `impact_score`, `possible_impact`
- `contact_level`, `contact_score`, `possible_contact`
- `severity_reasons`, `severity_floor_applied`, `severity_floor_reason`
- `classification_debug`
- `timeline_markers`

Possible next steps:

- Check `mimir_core_v2/evidence_extractor.py` thresholds such as `HARD_CLOSE_OBJECT_AREA_THRESHOLD`,
  `VISUAL_CONTACT_MIN_SCORE`, `OBJECT_CONTACT_HIGH_SCORE` — lower slightly if evidence is clearly
  present but under threshold.
- Increase sample rate by using `thorough` mode.
- Confirm the relevant camera angle is present in `camera_clips`.

Do not remove the severity resolver's hard floors (`mimir_core_v2/severity_resolver.py`). Real
impacts should not be downgradable to Ignore, by local rules or by AI.

## If Normal Traffic Is Flagged

Check:

- `impact_level`, `contact_level`, `possible_impact`, `possible_contact`
- `person_near_only`, `person_passby_evidence` vs. `person_interaction_evidence`
- `ai_evidence_review`
- `classification_debug`

If AI contributed to the flag, inspect the incident's own `ai_raw_response`, `ai_evidence`,
`ai_evidence_review`, and `ai_model` fields directly in `latest_session.json` — Core v2 stores AI
output on the incident itself rather than in a separate per-incident audit folder.

Possible next steps:

- Raise the relevant contact/impact thresholds in `evidence_extractor.py` slightly.
- Reduce AI budget during speed tests.
- Add feedback from the frontend so future training can learn from it (see `TRAINING_DATA_GUIDE.md`).

## If Video Paths Break

Check these incident fields: `video_path`, `library_video_path`, `source_video`,
`original_source_video`, `camera_clips`, `video_exists`, `storage_state`, `trash_video_path`.

The frontend tries safe video paths in this general order:

1. `video_path`
2. `library_video_path`
3. `source_video`
4. `original_source_video`
5. first camera clip path

If a file moved after review, make sure storage actions updated `library_video_path`,
`trash_video_path`, `camera_clips[].library_path`, `camera_clips[].trash_path`, `storage_state`.
Use the Files drawer in the desktop app to inspect the current location.

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

Inspect the incident's own AI fields in `latest_session.json`:

- `ai_raw_response` — the model's raw text response.
- `ai_evidence` / `ai_evidence_review` — parsed structured evidence.
- `ai_model` — which local model produced it.
- `ai_reviewed`, `ai_review_skipped_reason`, `ai_parse_error` — whether/why review ran.

Remember: AI is supporting evidence only. `mimir_core_v2/ai_enrichment.py` hard-codes
`can_change_final_severity: False` — local severity-resolver rules are the safety floor.

## Where Reports And Logs Are Saved

Backend:

```text
C:\Mimir_Backend\MimirOutputV2\latest_session.json
C:\Mimir_Backend\MimirOutputV2\sessions\<session_id>\session.json
C:\Mimir_Backend\MimirOutputV2\sessions\<session_id>\thumbnails\
```

Frontend/user logs:

```text
%USERPROFILE%\Documents\Mimir Logs\app_crash_log.txt
%USERPROFILE%\Documents\Mimir Feedback\
```

Training export (consented data only):

```text
C:\MimirTrainingDataset\
```

## Safe Debugging Habit

When tuning, use this loop:

```powershell
.venv\Scripts\python.exe -m py_compile mimir_core_v2_scan.py
.venv\Scripts\python.exe mimir_core_v2_scan.py --input "C:\mimir\test" --mode balanced
.venv\Scripts\python.exe inspect_latest_session.py
.venv\Scripts\python.exe -m unittest discover -s mimir_core_v2 -t . -p "test_*.py"
```

Make one small change at a time, then compare outputs.
