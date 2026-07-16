$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$BackendRoot = "C:\Mimir_Backend"
$BackendPython = Join-Path $BackendRoot ".venv\Scripts\python.exe"
$BackendBuildScript = Join-Path $BackendRoot "build_backend_exe.py"
$BackendDist = Join-Path $BackendRoot "dist_backend"
$ResourceBackend = Join-Path $Root "src-tauri\resources\mimir-backend"

if (!(Test-Path $BackendRoot)) {
  throw "Backend workspace not found: $BackendRoot"
}

if (!(Test-Path $BackendBuildScript)) {
  throw "Backend build script not found: $BackendBuildScript"
}

if (!(Test-Path $BackendPython)) {
  $BackendPython = "python"
}

& $BackendPython -m pip install --upgrade "pyinstaller>=6.21.0"
& $BackendPython $BackendBuildScript

New-Item -ItemType Directory -Force -Path $ResourceBackend | Out-Null

$RequiredExecutables = @(
  "mimir-core-v2-scan.exe",
  "mimir-core-v2-actions.exe"
)

foreach ($ExecutableName in $RequiredExecutables) {
  $Source = Join-Path $BackendDist $ExecutableName
  if (!(Test-Path $Source)) {
    throw "Expected backend executable was not built: $Source"
  }
  Copy-Item -Force $Source (Join-Path $ResourceBackend $ExecutableName)
}

$ReleaseCheck = Join-Path $BackendDist "mimir-core-v2-release-check.exe"
if (Test-Path $ReleaseCheck) {
  Copy-Item -Force $ReleaseCheck (Join-Path $ResourceBackend "mimir-core-v2-release-check.exe")
}

Write-Host "Built Core v2 backend resources: $ResourceBackend"
