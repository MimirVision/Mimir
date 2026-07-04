# Full AI Beta Installer Strategy

## Full AI Beta Goal

The Full AI Beta should feel like a normal consumer desktop app:

1. Install Mimir.
2. Open Mimir.
3. Confirm AI review is ready.
4. Choose a USB drive or footage folder.
5. Analyze footage.

Normal users should not need to understand the scanner internals, install developer tools, or run terminal commands before they can review incidents.

## What The Installer Should Include

The Full AI Beta installer should package everything needed for a complete local scan and review flow:

- Mimir desktop app.
- Bundled `mimir-backend` executable.
- Detector/model files needed by the scanner.
- Local AI runtime or setup needed to run AI review.
- Local vision model, or a guided model import/install step that happens inside Mimir.
- License and notice files for bundled runtimes, models, libraries, and third-party components.

If the model cannot legally or practically be bundled, the installer/app should still provide a guided setup path so the user does not need to find commands or instructions manually.

## What Users Should Not Need To Do

Normal Full AI Beta users should not need to:

- Install Python.
- Create a virtual environment.
- Install the backend manually.
- Type terminal commands.
- Manually install the AI model.

Developer workflows can still exist, but they should not appear as the normal product path.

## AI Readiness Rule

Full AI Beta requires AI review readiness before scanning.

If AI setup is broken or incomplete:

- Mimir blocks scanning.
- Mimir shows friendly repair/recheck UI.
- Mimir does not silently fall back to basic scanning.
- Mimir does not expose a standard/basic scan option in the normal UI.

The normal ready state should communicate that AI review is ready and the user can choose footage.

## Internal Pipeline Clarification

The product experience says "AI review," but the backend should still stay efficient and evidence-driven.

The scanner should:

- Group camera angles first.
- Run a fast local prepass.
- Use object, motion, impact, and contact evidence.
- Call AI only on candidate events.
- Avoid running AI once per camera angle.

This keeps scans faster, reduces noise, and helps prevent the AI model from becoming the sole decision maker.

## Clean Windows Acceptance Test

On a fresh Windows PC:

- There is no `C:\Mimir_Backend`.
- Python is not installed or required.
- No manual terminal setup is required.
- Install Mimir.
- Open Mimir.
- AI readiness passes.
- Select a USB drive or footage folder.
- Scan footage.
- Open an incident.
- Move a clip to Mimir Library.
- Move a clip to Mimir Trash.

This test should represent the expected beta user experience.

## Known Risks

The Full AI Beta packaging approach has real tradeoffs:

- Large installer size.
- Slow installation or first launch.
- Antivirus false positives.
- Weak hardware causing slow scans or AI startup failures.
- Model corruption during install or update.
- Model license and redistribution requirements.
- Local AI runtime startup time.

The installer and app should handle these with clear repair paths, integrity checks where practical, and friendly error messages.

## Later Option

A smaller Guided Setup installer may be offered later for users who prefer a lighter download.

For the main beta, the target remains Full AI Beta: install Mimir, open Mimir, AI review ready, scan footage.
