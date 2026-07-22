$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$BackendRoot = $env:MIMIR_BACKEND_ROOT
if ([string]::IsNullOrWhiteSpace($BackendRoot)) {
  $SiblingBackend = Join-Path (Split-Path -Parent $Root) "Mimir_Backend"
  $BackendRoot = if (Test-Path $SiblingBackend) { $SiblingBackend } else { "C:\Mimir_Backend" }
}
$BackendPython = Join-Path $BackendRoot ".venv-runtime\Scripts\python.exe"
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
  throw "Clean backend runtime environment not found: $BackendPython. Create .venv-runtime and install requirements-build.txt."
}

& $BackendPython -m PyInstaller --version
if ($LASTEXITCODE -ne 0) {
  throw "PyInstaller is not installed in the backend virtual environment. Install requirements-core-v2.txt first."
}
& $BackendPython $BackendBuildScript

New-Item -ItemType Directory -Force -Path $ResourceBackend | Out-Null

$RequiredExecutables = @(
  "mimir-core-v2-scan.exe",
  "mimir-core-v2-actions.exe",
  "mimir-core-v2-release-check.exe"
)

foreach ($ExecutableName in $RequiredExecutables) {
  $Source = Join-Path $BackendDist $ExecutableName
  if (!(Test-Path $Source)) {
    throw "Expected backend executable was not built: $Source"
  }
  Copy-Item -Force $Source (Join-Path $ResourceBackend $ExecutableName)
}

Write-Host "Built Core v2 backend resources: $ResourceBackend"
