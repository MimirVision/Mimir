# Mimir Tuning Guide

This guide explains the safest places to tweak scanner behavior in `mimir_core_v2` (Core v2), the
shipped scanner. Make small changes, test with known clips, and keep a copy of the previous
output for comparison. The earlier `tesla_ai_sorter.py` scanner has been removed — none of its
threshold names apply anymore.

## Where Settings Live

Most active thresholds are near the top of `mimir_core_v2/evidence_extractor.py`. Important ones:

- Motion: `MOTION_LOW_THRESHOLD`, `MOTION_MEDIUM_THRESHOLD`, `MOTION_HIGH_THRESHOLD`,
  `MOTION_SPIKE_MEDIUM_RATIO`, `MOTION_SPIKE_HIGH_RATIO`.
- Contact/impact: `LOCALIZED_CONTACT_THRESHOLD`, `LOCALIZED_CONTACT_HIGH_THRESHOLD`,
  `HARD_CLOSE_OBJECT_AREA_THRESHOLD`, `HARD_CLOSE_GLOBAL_MOTION_THRESHOLD`,
  `VISUAL_CONTACT_MIN_SCORE`, `OBJECT_CONTACT_MIN_SCORE`, `OBJECT_CONTACT_HIGH_SCORE`.
- No-detector fallback (used when object detection is disabled/unavailable):
  `NO_YOLO_LOCALIZED_MOTION_THRESHOLD`, `NO_YOLO_SPIKE_RATIO_THRESHOLD`,
  `NO_YOLO_CAMERA_SHAKE_THRESHOLD`, `NO_YOLO_MAX_MOTION_THRESHOLD`.
- Camera shake: `CAMERA_SHAKE_MEDIUM_THRESHOLD`, `CAMERA_SHAKE_HIGH_THRESHOLD`.
- Key moments: `KEY_MOMENT_MAX_COUNT`, `KEY_MOMENT_DEDUP_SEC`.
- AI budget: `AI_BUDGET_DEFAULTS` in `mimir_core_v2/cli.py`.

Severity floors/caps (what forces at least REVIEW or IMPORTANT, and what can never be downgraded)
live in `mimir_core_v2/severity_resolver.py`, in `resolve_severity()`.

## Tuning Motion Sensitivity

Motion sensitivity controls how easily Mimir treats a clip as having meaningful motion.

Relevant settings (`evidence_extractor.py`):

```python
MOTION_LOW_THRESHOLD = 0.28
MOTION_MEDIUM_THRESHOLD = 0.55
MOTION_HIGH_THRESHOLD = 0.82
```

If Mimir misses too much movement:

- Lower `MOTION_LOW_THRESHOLD` / `MOTION_MEDIUM_THRESHOLD` a little.
- Use `thorough` mode for more sampling.

If Mimir flags too much normal motion:

- Raise the relevant threshold slightly.
- Check whether normal passing traffic is being treated as contact/impact (see below).

Make small changes, not huge jumps.

## Tuning Impact and Contact Detection

Impact/contact logic protects real crashes from being ignored. Useful settings
(`evidence_extractor.py`):

```python
HARD_CLOSE_OBJECT_AREA_THRESHOLD = 0.70
HARD_CLOSE_GLOBAL_MOTION_THRESHOLD = 0.18
VISUAL_CONTACT_MIN_SCORE = 0.32
OBJECT_CONTACT_MIN_SCORE = 0.30
OBJECT_CONTACT_HIGH_SCORE = 0.48
```

If rear-end crashes are missed:

- Lower the relevant contact/impact threshold slightly.
- Check `severity_floor_applied` / `severity_floor_reason` and `classification_debug` on the
  incident to see whether local evidence reached the floor.
- Check `impact_score`, `impact_level`, `contact_score`, `contact_level`, `severity_reasons`.

If normal bumps become Important:

- Raise the relevant threshold values slightly.
- Check whether `possible_impact` / `possible_contact` is being set too easily.
- Look at `timeline_markers` to see which time was considered suspicious.

## Reducing False Important

False Important means Mimir is too aggressive. Check these fields in `latest_session.json`:

- `impact_level`, `impact_score`, `contact_level`, `contact_score`
- `possible_impact`, `possible_contact`
- `person_near_only`, `person_passby_evidence`, `person_interaction_evidence`
- `severity_reasons`, `severity_cap_applied`, `severity_cap_reason`, `classification_debug`

Common fixes:

- Raise the relevant contact/impact thresholds slightly.
- Check the incident's `ai_evidence_review` to see if AI review saw a misleading frame.
- Keep the severity resolver's hard floors intact. Do not remove protections for real
  impact/contact evidence (`mimir_core_v2/severity_resolver.py`).

## Reducing False Ignore

False Ignore means Mimir is missing things. Start by checking:

- Was an incident created for the group at all?
- Are `camera_clips` grouped correctly (`event_grouping.py`)?
- Did the primary camera miss the important angle (`primary_camera`, `available_cameras`)?

Possible tuning:

- Lower the relevant `evidence_extractor.py` threshold.
- Use `balanced` or `thorough` mode instead of `fast` for denser frame sampling
  (`frame_sampler.py`).
- Increase AI review budget for beta tests (`--ai-review-budget`).

Do not let AI downgrade strong local crash/contact evidence to Ignore — this is enforced in code
(`ai_enrichment.py` sets `can_change_final_severity: False`), not just a convention.

## AI Budget

AI review is intentionally budgeted so large scans do not take forever. Defaults
(`AI_BUDGET_DEFAULTS` in `mimir_core_v2/cli.py`):

```text
fast: 20
balanced: 50
thorough: 150
```

Override:

```powershell
.venv\Scripts\python.exe mimir_core_v2_scan.py --input "C:\mimir\test" --mode balanced --vlm qwen2.5vl:7b --ai-review-budget 25
```

Use a lower budget for speed, a higher budget when testing accuracy. The session reports
`ai_reviewed_groups`, `ai_skipped_groups`, `ai_failed_groups`, and `performance.ai_runtime_sec`.

## Testing Changes Safely

Use a small known test folder first:

```powershell
cd C:\Mimir_Backend
.venv\Scripts\python.exe -m py_compile mimir_core_v2_scan.py
.venv\Scripts\python.exe mimir_core_v2_scan.py --input "C:\mimir\test" --mode fast
.venv\Scripts\python.exe validate_mimir_output.py
.venv\Scripts\python.exe inspect_latest_session.py
```

Then test balanced, and if you changed AI-related behavior, add `--vlm qwen2.5vl:7b`. Always run
the unit test suite after threshold changes:

```powershell
.venv\Scripts\python.exe -m unittest discover -s mimir_core_v2 -t . -p "test_*.py"
```

## What To Compare

After each tweak, compare:

- Incident count and Important/Review/Ignore counts.
- `severity_reasons`, `severity_floor_applied`, `severity_cap_applied`, `classification_debug`.
- `performance` block in `latest_session.json`.
- Whether the unit test suite still passes.

Good tuning changes should improve a known problem without breaking known important events.
