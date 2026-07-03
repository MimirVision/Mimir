# Mimir Source Compatibility Tests

This checklist documents the input folder shapes Mimir must support before release.

Release requirement:

A consumer must be able to plug in the USB drive, select the drive root, and get a useful result without manually navigating into subfolders.

## Commands

Run source discovery only:

```powershell
python discover_footage_source.py --input "<path>"
```

Run a full scan:

```powershell
python tesla_ai_sorter.py --input "<path>" --mode balanced --vlm qwen2.5vl:7b
```

## Test Cases

### 1. USB Drive Root

Input:

```text
E:\
```

Expected:

- Finds `E:\TeslaCam`
- Scans `SentryClips`, `SavedClips`, and `RecentClips` if present
- Does not recursively scan the entire drive when `TeslaCam` exists
- Reports source type as USB/root-style source

### 2. TeslaCam Root

Input:

```text
E:\TeslaCam
```

Expected:

- Scans known categories:
  - `SentryClips`
  - `SavedClips`
  - `RecentClips`
- Reports TeslaCam root found

### 3. SentryClips Folder

Input:

```text
E:\TeslaCam\SentryClips
```

Expected:

- Scans sentry event folders and clips
- Reports `SentryClips` as a found category
- Uses sentry-style event grouping when filenames support it

### 4. SavedClips Folder

Input:

```text
E:\TeslaCam\SavedClips
```

Expected:

- Scans saved clips
- Reports `SavedClips` as a found category

### 5. RecentClips Folder

Input:

```text
E:\TeslaCam\RecentClips
```

Expected:

- Scans rolling clips
- Warns that `RecentClips` may not represent saved incidents
- Reports `RecentClips` as a found category

### 6. Single Event Folder

Input:

```text
E:\TeslaCam\SentryClips\2026-03-03_10-46-26
```

Expected:

- Scans clips in that event folder
- Reads `event.json` if present
- Preserves source event metadata in output

### 7. Generic MP4 Folder

Input:

```text
C:\mimir\test
```

Expected:

- Scans MP4 files as generic footage
- Does not require a `TeslaCam` folder
- Reports a warning that no `TeslaCam` folder was found

### 8. Empty Folder

Input:

```text
<empty folder>
```

Expected:

- Does not crash
- Shows a friendly no footage found error
- Reports `source_report.is_supported` as `false`
- User-facing message:

```text
No footage was found. Select the USB drive, TeslaCam folder, or a folder containing MP4 clips.
```

### 9. Incomplete Camera Group

Input:

```text
E:\TeslaCam\SentryClips\<event folder with only some camera files>
```

Expected:

- Scans available clips
- Warns that common camera angles are missing
- Does not fail because one or more cameras are absent

### 10. Unknown Camera Suffix

Input:

```text
E:\TeslaCam\SentryClips\2026-03-03_10-46-26-custom_camera.mp4
```

Expected:

- Preserves the unknown suffix in discovery output
- Scans the file if it is a valid MP4
- Adds a warning only
- Does not crash or reject the whole source

## Pass Criteria

- `discover_footage_source.py` prints a readable summary for each source shape.
- `tesla_ai_sorter.py` writes `latest_session.json` for supported source shapes.
- `latest_session.json` includes a useful `source_report`.
- Selecting a USB drive root works for a non-technical user.
- Empty or unsupported folders produce a clear no-footage message, not a traceback.
