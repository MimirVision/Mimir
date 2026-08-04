# Mimir Output Schema

The main output file is:

```text
C:\Mimir_Backend\MimirOutput\latest_session.json
```

This file is what the Tauri/React app loads to show scan results. Fields are additive over time, so frontend/backend tools should read missing fields safely.

## Top-Level Session Fields

Common fields:

- `status`: usually `running` or `complete`.
- `started_at`, `finished_at`: scan timestamps.
- `input_folder`: folder passed to the scanner.
- `selected_input`: resolved input folder.
- `detected_source_type`: for example `generic_folder` or TeslaCam-related values.
- `scan_roots`: folders scanned.
- `source_report`: user-friendly source discovery summary.
- `scan_pipeline`: expected to be `two_pass_grouped`.
- `incidents`: list of incident objects.

Counts:

- `clips_processed`
- `important`
- `review`
- `ignore`
- `event_groups_found`
- `multi_camera_groups`
- `single_camera_groups`

## Incidents

Each item in `incidents` is one reviewable event.

Important fields:

- `id`: incident id, such as `incident_0001`.
- `source_video`: original source video path.
- `original_source_video`: original source path kept for compatibility.
- `video_path`: video the UI should try first.
- `video_exists`: whether the current video path existed when written.
- `created_at`: incident timestamp.
- `summary`: short description.
- `evidence`: short evidence strings.
- `recommended_action`: simple action text.

Image fields:

- `hero_thumbnail`
- `thumbnail`
- `start_frame_image`
- `best_frame_image`
- `end_frame_image`
- `contact_sheet`

The UI should handle any image being missing.

## Event Group Fields

Grouped multi-camera incidents may include:

- `event_group_id`: stable group id, usually based on timestamp/folder.
- `event_timestamp`: event timestamp if known.
- `event_folder`: source event folder.
- `source_category`: `SentryClips`, `SavedClips`, `RecentClips`, or generic.
- `camera_count`: number of camera clips.
- `available_cameras`: camera names found.
- `primary_camera`: chosen camera for thumbnail/viewer.

## Camera Clips

`camera_clips` lists camera files for a grouped incident.

Typical item:

```json
{
  "camera": "front",
  "path": "C:\\path\\clip-front.mp4",
  "filename": "2026-03-03_14-31-54-front.mp4",
  "duration_sec": 60.0,
  "exists": true,
  "library_path": "C:\\Users\\you\\Videos\\Mimir Library\\Review\\clip-front.mp4",
  "trash_path": null
}
```

Older or partial outputs may have fewer fields. Code should accept either a list or object-style camera map.

## Severity Fields

Important severity fields:

- `severity`: current severity used by the UI.
- `final_severity`: scanner’s final resolved severity.
- `pre_escalation_severity`: severity before resolver escalation.
- `user_status`: user override/status if review actions were used.
- `manual_status_override`: true if the user manually changed status.
- `severity_reasons`: readable reasons for final severity.
- `final_decision_source`: for example `local_rules`, `ai_supported`, or `ai_escalated`.

Valid severity values:

- `IMPORTANT`
- `REVIEW`
- `IGNORE`

## AI Evidence Review

`ai_evidence_review` contains structured evidence from the local vision model when AI was used.

Typical fields:

- `scene_type`
- `visible_person`
- `visible_vehicle_close`
- `visible_contact`
- `visible_impact`
- `normal_passing_traffic`
- `evidence`
- `concerns`
- `recommended_severity`
- `confidence`
- `raw_response`
- `ai_parse_error`
- `ai_review_skipped_reason`

If AI was skipped or unavailable, this may contain fallback evidence.

Audit files, when present:

- session: `ai_audit_enabled`, `ai_audit_folder`
- incident: `ai_audit_folder`

## Classification Debug

`classification_debug` explains how Mimir decided.

Useful fields:

- `local_rule_severity`
- `ai_recommended_severity`
- `final_severity`
- `ai_confidence`
- `final_decision_source`
- `ai_allowed_to_change`
- `ai_blocked_reason`
- `ai_review_skipped_reason`
- `impact_level`
- `impact_score`
- `contact_level`
- `contact_score`
- `possible_impact`
- `possible_contact`
- `crash_safety_triggered`
- `severity_reasons`

Use this section when a result looks wrong.

## Timeline Markers

`timeline_markers` is a list of moments shown in the video timeline.

Marker fields:

- `time_sec`
- `frame_index`
- `type`
- `severity`
- `label`
- `description`

The field names should remain stable because the UI uses them.

## Performance

Top-level `performance` summarizes scan speed:

- `total_runtime_sec`
- `total_video_duration_sec`
- `total_ai_calls`
- `total_sampled_frames`
- `total_event_groups`
- `stage_timings`
- `slowest_groups`
- `videos_processed`
- `avg_sec_per_video`
- `frames_sampled`
- `ai_calls`
- `incidents_created`

Pipeline fields may also appear top-level:

- `prepass_groups_processed`
- `prepass_candidates_found`
- `deep_analysis_groups`
- `skipped_low_interest_groups`
- `prepass_runtime_sec`
- `deep_analysis_runtime_sec`

## Storage Fields

These fields are updated after review/storage actions:

- `storage_state`: for example `library`, `trash`, `partial_library`, or `partial_trash`.
- `library_video_path`
- `trash_video_path`
- `original_source_video`
- `moved_to_library`
- `user_deleted`
- `storage_action_applied`
- `camera_clips[].library_path`
- `camera_clips[].trash_path`

Session storage fields:

- `library_root`
- `library_scan_folder`
- `source_action`
- `files_copied`
- `files_moved`
- `files_failed`
- `usb_cleanup_performed`
- `source_files_removed`
- `storage_warnings`

## Cache Fields

Cache fields are optional. If present, tools may show:

- `cache_hits`
- `cache_misses`

If absent, treat them as `0` or “not reported.” Do not crash on missing cache fields.

