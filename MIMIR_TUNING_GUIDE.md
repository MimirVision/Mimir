# Mimir Tuning Guide

This guide explains the safest places to tweak scanner behavior. Make small changes, test with known clips, and keep a copy of the previous output for comparison.

## Where Settings Live

Most active thresholds are near the top of `tesla_ai_sorter.py`.

Important sections:

- `SCAN_MODE_CONFIGS`: fast/balanced/quality/thorough sample rates and prepass thresholds.
- Detection tuning: `MIN_CONF`, `MIN_AREA_RATIO`.
- Event logic: `EVENT_TRIGGER`, `EVENT_END_TIMEOUT`, `MIN_EVENT_FRAMES`.
- Motion/impact tuning: `MOTION_SPIKE_THRESHOLD`, `CRASH_GLOBAL_MOTION_TRIGGER`, `CRASH_SCENE_CHANGE_TRIGGER`, `CRASH_FLOW_TRIGGER`.
- AI budget: `DEFAULT_AI_REVIEW_BUDGETS`.
- Top crop: `IGNORE_TOP_RATIO`.

`config.py` also contains base folders and model filenames.

## Tuning Motion Sensitivity

Motion sensitivity controls how easily Mimir starts an event.

Relevant settings:

```python
EVENT_TRIGGER = 14.0
MOTION_SPIKE_THRESHOLD = 24.0
CRASH_GLOBAL_MOTION_TRIGGER = 28.0
```

If Mimir misses too much movement:

- Lower `EVENT_TRIGGER` a little.
- Lower `MOTION_SPIKE_THRESHOLD` a little.
- Use `quality` or `thorough` mode for more sampling.

If Mimir flags too much normal motion:

- Raise `EVENT_TRIGGER` slightly.
- Raise prepass thresholds in `SCAN_MODE_CONFIGS`.
- Check whether normal passing traffic is being treated as contact/impact.

Make small changes, such as `14.0` to `16.0`, not huge jumps.

## Tuning Impact and Contact Detection

Impact/contact logic protects real crashes from being ignored.

Useful settings:

```python
CRASH_GLOBAL_MOTION_TRIGGER = 28.0
CRASH_SCENE_CHANGE_TRIGGER = 18.0
CRASH_FLOW_TRIGGER = 2.6
CRASH_IMPACT_SPIKE_TRIGGER = 52.0
IMPACT_FOCUSED_CONTACT_SHEET_SCORE = 0.45
```

If rear-end crashes are missed:

- Lower crash trigger values slightly.
- Check `classification_debug` for `crash_safety_triggered`.
- Check `impact_score`, `impact_level`, and `severity_reasons`.

If normal bumps become Important:

- Raise crash trigger values slightly.
- Check whether `possible_impact` is being set too easily.
- Look at timeline markers to see which time was considered suspicious.

## Reducing False Important

False Important means Mimir is too aggressive.

Check these fields in `latest_session.json`:

- `impact_level`
- `impact_score`
- `contact_level`
- `contact_score`
- `possible_impact`
- `possible_contact`
- `normal_passing_traffic_evidence`
- `severity_reasons`
- `classification_debug`

Common fixes:

- Raise contact/impact thresholds slightly.
- Make prepass less eager by raising `prepass_deep_threshold`.
- Check AI audit files to see if AI saw a misleading frame.
- Keep crash safety floors intact. Do not remove protections for real impact/contact.

## Reducing False Ignore

False Ignore means Mimir is missing things.

Start by checking:

- Was an incident created?
- Was `deep_analysis_performed` false?
- Was the group skipped as low interest?
- Are `camera_clips` grouped correctly?
- Did the primary camera miss the important angle?

Possible tuning:

- Lower `prepass_deep_threshold`.
- Lower `prepass_uncertain_threshold`.
- Increase `prepass_max_samples_per_camera`.
- Use `balanced` or `quality` mode instead of `fast`.
- Increase AI review budget for beta tests.

Do not let AI downgrade strong local crash/contact evidence to Ignore.

## AI Budget

AI review is intentionally budgeted so large scans do not take forever.

Defaults:

```text
fast: 20
balanced: 50
quality/thorough: 150
```

Override:

```powershell
python tesla_ai_sorter.py --input "C:\mimir\test" --mode balanced --vlm qwen2.5vl:7b --ai-review-budget 25
```

Use a lower budget for speed. Use a higher budget when testing accuracy.

The session reports:

- `ai_review_budget`
- `ai_review_candidates`
- `ai_reviewed_groups`
- `ai_skipped_groups`
- `ai_review_runtime_sec`

## Testing Changes Safely

Use a small known test folder first:

```powershell
cd C:\Mimir_Backend
python -m py_compile tesla_ai_sorter.py
python tesla_ai_sorter.py --input "C:\mimir\test" --mode fast
python validate_mimir_output.py
python inspect_latest_session.py
```

Then test balanced:

```powershell
python tesla_ai_sorter.py --input "C:\mimir\test" --mode balanced
python validate_mimir_output.py
python inspect_latest_session.py
```

If you changed AI-related behavior:

```powershell
python tesla_ai_sorter.py --input "C:\mimir\test" --mode balanced --vlm qwen2.5vl:7b
python validate_mimir_output.py
```

For performance:

```powershell
python benchmark_large_scan.py --input "C:\mimir\test" --mode balanced --max-runtime-min 60
```

## What To Compare

After each tweak, compare:

- incident count
- Important/Review/Ignore counts
- `severity_reasons`
- `classification_debug`
- `performance_report.json`
- AI audit folders, if AI was used

Good tuning changes should improve a known problem without breaking known important events.

