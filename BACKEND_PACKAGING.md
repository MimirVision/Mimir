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
C:\Mimir_Backend\dist_backend\mimir-core-v2-actions.exe
```

If `mimir_core_v2_release_check.py` exists, the script also builds:

```text
C:\Mimir_Backend\dist_backend\mimir-core-v2-release-check.exe
```

## Build Command

From `C:\Mimir_Backend`:

```powershell
python build_backend_exe.py
```

The script first runs compile checks, then runs PyInstaller.

If PyInstaller is missing, install it in the backend virtual environment:

```powershell
python -m pip install pyinstaller
```

Then run:

```powershell
python build_backend_exe.py
```

## Test Commands

After building, test the scanner executable:

```powershell
dist_backend\mimir-core-v2-scan.exe --input "D:\TeslaCam\SentryClips\2026-04-18_16-04-02" --mode balanced
```

Then test the action executable:

```powershell
dist_backend\mimir-core-v2-actions.exe --list-incidents
```

Optional release-check executable:

```powershell
dist_backend\mimir-core-v2-release-check.exe --input "D:\TeslaCam\SentryClips\2026-04-18_16-04-02"
```

## Output Folder

For this beta packaging pass, backend output still goes to:

```text
C:\Mimir_Backend\MimirOutputV2\
```

This keeps the packaged backend compatible with the current frontend while testing.

## Known Limitations

- This is beta packaging, not the final installer architecture.
- The actions and release-check executables use packaging wrappers that expect the backend folder to remain at `C:\Mimir_Backend`.
- The final installer should eventually choose an app-owned data folder instead of writing directly to `C:\Mimir_Backend`.
- Model files and detector dependencies may make executable size large.
- Antivirus tools may flag new unsigned executables until the installer/signing flow is finalized.
- If local AI runtime/model support is bundled later, the installer will need a separate readiness and repair flow.

## Safety Notes

- Packaging does not change scanner behavior.
- Packaging does not move or delete source footage.
- The scan executable writes scan results only.
- The actions executable can move reviewed clips only when explicitly called with a move action.
- `Move to Mimir Trash` means moving files into the Mimir Library trash folder, not permanent deletion.
