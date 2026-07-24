# Mimir Frontend Debugging

This guide explains what to check when the desktop app behaves strangely.

## First Checks

From the frontend folder:

```powershell
cd C:\Mimir
npm run build
npm run type-check
```

If Tauri/Rust commands changed:

```powershell
cd C:\Mimir\src-tauri
cargo check
```

For local desktop testing:

```powershell
cd C:\Mimir
npm run desktop:dev
```

## If The App Crashes On Click

Most incident surfaces are wrapped in `CrashSafeBoundary`.

Check:

- Did the friendly error panel appear?
- Did Back to review work?
- Did Copy diagnostics include the incident id and video path?
- Did a log entry appear?

Log path:

```text
%USERPROFILE%\Documents\Mimir Logs\app_crash_log.txt
```

Common causes:

- `latest_session.json` has a missing or malformed field.
- `timeline_markers` is not an array.
- an incident has no usable `id`.
- an image/video path is not a string.

Useful files:

- `src\components\CrashSafeBoundary.tsx`
- `src\components\IncidentLibraryView.tsx`
- `src\components\IncidentViewerScreen.tsx`

## If Video Does Not Play

The viewer tries several paths:

1. `video_path`
2. `library_video_path`
3. `source_video`
4. `original_source_video`
5. first `camera_clips` path

Check the incident in `latest_session.json`:

- Does the path exist?
- Is it an absolute Windows path?
- Is the extension a video extension?
- Is `video_exists` false?
- Was the clip moved to Mimir Library or Mimir Trash?

The frontend uses:

```ts
convertFileSrc(path, 'asset')
```

Do not pass raw Windows paths directly to `<video>` or `<img>`.

If video is missing, the viewer should keep incident details visible and show an image fallback when possible.

## If Thumbnails Do Not Load

Check these fields:

- `hero_thumbnail`
- `thumbnail`
- `best_frame_image`
- `contact_sheet`

The image file must exist locally. If one image fails, cards try the next available image.

Common issues:

- The backend output folder was deleted.
- The file was moved but the session JSON still points to the old path.
- The path is relative instead of absolute.

Useful component:

```text
src\components\IncidentLibraryView.tsx
```

Look for `IncidentImage`.

## If Scan Progress Is Stuck

The backend emits `MIMIR_PROGRESS` lines. Tauri reads scanner stdout and sends progress events to React.

Check:

- Is the Python scanner still running?
- Did `run_local_scan` return an error?
- Are there progress lines in the terminal?
- Is the selected folder huge?
- Is local AI taking a long time?

Useful files:

- `src\App.tsx`
- `src\components\ActiveScanStatus.tsx`
- `src-tauri\src\main.rs`

Backend performance data is in the session's `performance` block:

```text
C:\Mimir_Backend\MimirOutputV2\latest_session.json
```

## If Review Actions Fail

Review actions call one of:

```ts
invoke('run_core_v2_storage_action', ...)
invoke('save_manual_status', ...)
```

These are handled by Tauri and the backend clip action script.

Check:

- Is `latest_session.json` present?
- Does the incident have an `id`?
- Is the source file still present?
- Is the Mimir Library folder writable?

The UI should show an inline error instead of crashing.

## If Feedback Does Not Save

AI feedback is saved by:

```ts
invoke('save_incident_feedback', ...)
```

Output folder:

```text
%USERPROFILE%\Documents\Mimir Feedback\
```

Check:

- Is Documents writable?
- Did the user select a feedback button?
- If Include video clip was checked, does the video path exist?

If video copying fails, feedback should still save with a `video_copy_error` field.

## Where Logs Are Saved

Frontend crash diagnostics:

```text
%USERPROFILE%\Documents\Mimir Logs\app_crash_log.txt
```

Feedback packages:

```text
%USERPROFILE%\Documents\Mimir Feedback\
```

Backend scan outputs:

```text
C:\Mimir_Backend\MimirOutputV2\latest_session.json
C:\Mimir_Backend\MimirOutputV2\sessions\<session_id>\session.json
```

Development terminal logs may also exist in the repo, such as:

```text
tauri-dev*.log
tauri-sidecar*.log
```

These are local development artifacts.

## How To Copy Diagnostics

When an error boundary appears:

1. Click Copy diagnostics.
2. Paste the text into an issue or notes file.
3. Include what you clicked right before the error.

Diagnostics usually include:

- timestamp
- incident id
- attempted video path
- error message
- stack trace if available

If Copy diagnostics is not available, open:

```text
%USERPROFILE%\Documents\Mimir Logs\app_crash_log.txt
```

## Safe Debugging Loop

Use this loop after frontend edits:

```powershell
cd C:\Mimir
npm run build
npm run type-check
cd C:\Mimir\src-tauri
cargo check
```

Then manually test:

- Load latest session.
- Open Important, Review, Ignore, All, and Trash tabs.
- Open an incident.
- Click timeline markers.
- Open Files drawer.
- Save AI feedback without video.
- Save AI feedback with video only on a small test clip.

