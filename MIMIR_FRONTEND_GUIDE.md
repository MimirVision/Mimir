# Mimir Frontend Guide

This guide explains the Tauri + React + TypeScript app in beginner-friendly terms. The frontend loads scan results, starts scans, shows incidents, opens videos safely, and lets users review or organize clips.

## App Structure

Important folders:

```text
C:\Mimir\
  src\
    App.tsx
    types.ts
    index.css
    components\
  src-tauri\
    src\main.rs
```

Important files:

- `src\App.tsx`: main app state and screen switching.
- `src\types.ts`: TypeScript shapes for sessions, incidents, progress, and system checks.
- `src\index.css`: global colors, base styling, scrollbar, and Tailwind setup.
- `src\components\ImportPanel.tsx`: import/start scan screen.
- `src\components\ScanProgress.tsx`: scan progress UI.
- `src\components\IncidentLibraryView.tsx`: review grid and in-app library.
- `src\components\IncidentViewerScreen.tsx`: incident viewer, video player, timeline, actions, and AI feedback.
- `src\components\CrashSafeBoundary.tsx`: React error boundary and diagnostics UI.
- `src-tauri\src\main.rs`: Rust/Tauri bridge to the Python backend and local filesystem.

## Main Screens

The app has two main states:

- Import/start scan screen.
- Results/review/library screen.

`App.tsx` controls this with `appView`.

```ts
type AppView = 'import' | 'library'
```

When a scan finishes or the latest session loads, the app shows `IncidentLibraryView`.

## Import Screen

File:

```text
src\components\ImportPanel.tsx
```

The import screen lets the user:

- choose a folder
- drag and drop a folder
- pick scan mode
- enable enhanced local AI review if available
- start a scan

The scan starts from `App.tsx` by calling the Tauri command:

```ts
invoke('run_local_scan', { selectedFolder, mode, useEnhancedAiReview, selectedModel })
```

The Rust side then runs the Python scanner.

## Scan Progress Screen

File:

```text
src\components\ScanProgress.tsx
```

The backend prints progress lines beginning with `MIMIR_PROGRESS`. Tauri reads those lines and emits them to the frontend. `App.tsx` listens for progress events and updates:

- `scanProgress`
- `lastProgressMessage`
- `scanState`

Useful frontend type:

```ts
BackendProgress
```

## Review Page

File:

```text
src\components\IncidentLibraryView.tsx
```

The review page is the main place users browse incidents. It shows:

- scan summary
- Important / Review / Ignore counts
- tabs for Important, Review, Ignore, All, and Trash
- compact incident cards
- multi-select toolbar
- Free up USB modal
- Files drawer

Important helper components:

- `FilterChip`
- `IncidentCard`
- `SelectionToolbar`
- `FilesDrawer`

The review grid uses incident data from `latest_session.json`. It does not invent mock incidents.

## Incident Viewer

File:

```text
src\components\IncidentViewerScreen.tsx
```

The viewer shows one incident at a time:

- video or fallback image
- timeline markers
- status controls
- move to Mimir Library / Mimir Trash
- file location panel
- AI feedback panel
- review notes
- technical details

Important helper components:

- `ViewerMedia`
- `IncidentTimelineMarkers`
- `ReviewActionsPanel`
- `AiFeedbackPanel`
- `DetailsPanel`

The viewer resolves video paths safely. It tries:

1. `video_path`
2. `library_video_path`
3. `source_video`
4. `original_source_video`
5. first `camera_clips` path

Local files are passed through Tauri-safe asset URLs using:

```ts
convertFileSrc(path, 'asset')
```

## Files Drawer

File:

```text
src\components\IncidentLibraryView.tsx
```

The Files drawer is opened from incident cards or library items. It shows:

- storage state
- current location
- original/source folder
- camera clips
- Open in Explorer button

The drawer is intentionally not a full file explorer. It is a small “where are my clips?” panel.

## In-App Library

File:

```text
src\components\IncidentLibraryView.tsx
```

The in-app library is a curated browser for latest-session incidents. It has sections:

- Important
- Review
- Ignore
- Trash

It shows moved paths when available, but it does not crawl the filesystem by itself. It reads from `latest_session.json`.

Important helper components:

- `LibrarySection`
- `LibraryItem`
- `StorageStateBadge`

## Feedback UI

File:

```text
src\components\IncidentViewerScreen.tsx
```

The AI feedback panel lets testers choose:

- Correct
- Should be Important
- Should be Review
- Should be Ignore
- Weird AI flag
- Missed obvious event

Optional fields:

- Notes
- Include video clip

Feedback is saved locally with the Tauri command:

```ts
invoke('save_incident_feedback', { feedback, includeVideo, videoPath })
```

Files go to:

```text
%USERPROFILE%\Documents\Mimir Feedback\
```

No upload is performed.

## Loading latest_session.json

`App.tsx` loads the latest session with:

```ts
invoke<string>('load_latest_session_json')
```

The string is parsed into a `MimirSession` object. If loading fails, the app shows a friendly state instead of crashing.

The Rust command lives in:

```text
src-tauri\src\main.rs
```

The backend source of truth is usually:

```text
C:\Mimir_Backend\MimirOutput\latest_session.json
```

## Backend Commands

Frontend calls Tauri commands with `invoke`.

Common commands:

- `count_teslacam_clips`: count clips before scanning.
- `run_local_scan`: run the Python scanner.
- `load_latest_session_json`: load scan results.
- `run_incident_action`: set status or move clips after review.
- `save_incident_note`: save a user note.
- `save_incident_feedback`: save local beta feedback.
- `open_containing_folder`: open Explorer for a file/folder.
- `open_mimir_storage_folder`: open Mimir Library or Trash.
- `log_incident_diagnostic`: write crash diagnostics.

## Error Handling

Main error handling places:

- `CrashSafeBoundary.tsx`: catches React render errors and shows recovery UI.
- `IncidentLibraryView.tsx`: wraps library and viewer.
- `IncidentViewerScreen.tsx`: wraps video player, timeline, action panel, and details.
- `src-tauri\src\main.rs`: returns `ScanFailure` objects instead of panicking.

Frontend diagnostics are saved to:

```text
%USERPROFILE%\Documents\Mimir Logs\app_crash_log.txt
```

The viewer also has a Copy diagnostics button when an error boundary catches a problem.

## Useful Commands

```powershell
cd C:\Mimir
npm run dev
npm run build
npm run type-check
npm run desktop:dev
```

For Rust/Tauri command checks:

```powershell
cd C:\Mimir\src-tauri
cargo check
```

