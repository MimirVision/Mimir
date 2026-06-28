# Mimir

Mimir is a local-first desktop app for reviewing vehicle and dashcam footage. It scans clips, finds activity around the car, explains why a clip was flagged, and sorts results into clear review priorities.

## Customer experience

The consumer build is designed to be simple:

1. Install Mimir.
2. Open Mimir.
3. Select the footage folder.
4. Start a scan.
5. Review flagged clips with confidence, evidence, and a plain-language explanation.

The Windows installer now includes the local backend sidecar, the vision model, and the desktop UI. Customers should not need to install Python or start an API manually.

## What Mimir analyzes

Mimir currently analyzes:

- People detected near the vehicle
- Nearby vehicles
- Sustained activity across sampled frames
- The strongest evidence frame for each event
- Optional local AI review when enabled

Each flagged event includes:

- Decision label
- Confidence percentage
- Reason for the decision
- Findings used in the decision
- Suggested next action
- Evidence frame

## Local-first privacy

The first product version runs locally on the customer's computer. Footage does not need to be uploaded to a cloud service for scanning.

## Project structure

```text
src/                    React/Tauri desktop UI
src-tauri/              Tauri shell and installer config
api.py                  FastAPI bridge used by the local sidecar
mimir_core.py           Scanner and event classification logic
backend_entry.py        PyInstaller entrypoint for the sidecar
scripts/build-sidecar.ps1
yolov8n.pt              Local vision model
```

## Development setup

Install JavaScript dependencies:

```powershell
npm install
```

Install Python 3.12, then create the project Python environment:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Run for development

Start the backend manually:

```powershell
npm run api
```

In another terminal, start the desktop app:

```powershell
npm run desktop:dev
```

## Build the consumer installer

```powershell
npm run desktop:build
```

This builds the Python backend sidecar, then builds the Tauri app and installers.

Build output:

```text
src-tauri/target/release/bundle/msi/
src-tauri/target/release/bundle/nsis/
```

## Cloud path

Cloud should come after the local desktop product is smooth:

1. Keep private local scanning as the default.
2. Add account licensing and code signing.
3. Add optional cloud sync for reports and evidence frames.
4. Add browser/phone access for already-processed events.
5. Add upload-based scanning only when storage, privacy, and billing are ready.
