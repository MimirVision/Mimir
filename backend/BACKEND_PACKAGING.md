# Mimir Core v2 Backend Packaging

This document describes the current beta packaging path for the Mimir Core v2 backend.

The goal is to produce Windows executables that the Tauri app can call without requiring a normal user to install Python, create a virtual environment, or run backend commands by hand.

## What Gets Built

The packaging script creates executables in:

```text
C:\Mimir_Backend\dist_backend\
```

Expected files:

```text
C:\Mimir_Backend\dist_backend\mimir-core-v2-scan.exe
C:\Mimir_Backend\dist_backend\mimir-core-v2-ai-enrich.exe
C:\Mimir_Backend\dist_backend\mimir-core-v2-actions.exe
C:\Mimir_Backend\dist_backend\mimir-core-v2-dataset.exe
```

If `mimir_core_v2_release_check.py` exists, the script also builds this
developer-only release tool in the backend distribution folder:

```text
C:\Mimir_Backend\dist_backend\mimir-core-v2-release-check.exe
```

The desktop installer does not bundle this executable. Release checks run in
the controlled build workspace and are not part of the customer runtime.

## Build Command

From `C:\Mimir_Backend`:

```powershell
.venv-runtime\Scripts\python.exe build_backend_exe.py
```

The script first runs compile checks, then runs PyInstaller.

If PyInstaller is missing, install it in the backend virtual environment:

```powershell
.venv-runtime\Scripts\python.exe -m pip install -r requirements-build.txt
```

Then run:

```powershell
.venv-runtime\Scripts\python.exe build_backend_exe.py
```

## Test Commands

After building, test the scanner executable:

```powershell
dist_backend\mimir-core-v2-scan.exe --input "D:\TeslaCam\SentryClips\2026-04-18_16-04-02" --mode balanced
```

Run this from a directory outside `C:\Mimir_Backend` as a clean runtime smoke
test. The executable contains the Core v2 modules, ONNX model, manifest, and
model license; it does not import source files from the developer workspace.

Then test the action executable:

```powershell
dist_backend\mimir-core-v2-actions.exe --list-incidents
```

Optional release-check executable:

```powershell
dist_backend\mimir-core-v2-release-check.exe --input "D:\TeslaCam\SentryClips\2026-04-18_16-04-02"
```

Run the complete reliability gate against the packaged scanner after all
required real-footage fixtures are available:

```powershell
.venv-runtime\Scripts\python.exe mimir_core_v2_reliability.py `
  --scanner "dist_backend\mimir-core-v2-scan.exe" `
  --iterations 500 `
  --report "C:\Mimir\release_assets\reliability_report.json"
```

`--allow-missing-required` is for developer smoke runs only. Reports produced
without every required fixture cannot pass the release gate.

## Output Folder

The scanner always receives an explicit output directory from the desktop app.
Release builds use the application data directory. Development builds may use:

```text
C:\Mimir_Backend\MimirOutputV2\
```

Set `MIMIR_OUTPUT_DIR` to an absolute path for an explicit development override.

## Known Limitations

- This is beta packaging and the installer still requires signing and clean-VM evidence before external distribution.
- Debug builds deliberately support sibling developer scripts and executables; release builds resolve bundled sidecars only.
- Model files and detector dependencies may make executable size large.
- Antivirus tools may flag new unsigned executables until the installer/signing flow is finalized.
- Local AI remains an optional external Labs dependency and runs only after local results are ready.

## Safety Notes

- Packaging does not change scanner behavior.
- Packaging does not move or delete source footage.
- The scan executable writes scan results only.
- The actions executable can move reviewed clips only when explicitly called with a move action.
- `Move to Mimir Trash` means moving files into the Mimir Library trash folder, not permanent deletion.
