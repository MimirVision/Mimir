$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Binaries = Join-Path $Root "src-tauri\binaries"
$DistExe = Join-Path $Root "dist-backend\mimir-api.exe"
$SidecarExe = Join-Path $Binaries "mimir-api-x86_64-pc-windows-msvc.exe"

if (!(Test-Path $Python)) {
  throw "Python environment not found. Run: python -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r requirements.txt"
}

& $Python -m pip install pyinstaller==6.11.1

New-Item -ItemType Directory -Force -Path $Binaries | Out-Null

& $Python -m PyInstaller `
  --noconfirm `
  --clean `
  --onefile `
  --name mimir-api `
  --distpath (Join-Path $Root "dist-backend") `
  --workpath (Join-Path $Root "build-backend") `
  --add-data "$Root\yolov8n.pt;." `
  "$Root\backend_entry.py"

Copy-Item -Force $DistExe $SidecarExe
Write-Host "Built sidecar: $SidecarExe"
