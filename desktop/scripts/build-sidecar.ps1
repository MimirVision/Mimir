$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$BackendRoot = $env:MIMIR_BACKEND_ROOT
if ([string]::IsNullOrWhiteSpace($BackendRoot)) {
  # The backend is a sibling directory in the monorepo. Resolved relative to
  # this script rather than hardcoded, so a clone anywhere -- another machine,
  # a CI runner, a second working copy -- still finds it. The old lookup was
  # for the pre-monorepo "Mimir_Backend" sibling and only worked because it
  # fell through to an absolute path that happened to be right on one machine.
  $SiblingBackend = Join-Path (Split-Path -Parent $Root) "backend"
  $BackendRoot = if (Test-Path $SiblingBackend) { $SiblingBackend } else { "C:\MimirDev\backend" }
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
if ($LASTEXITCODE -ne 0) {
  throw "Backend sidecar build failed (exit $LASTEXITCODE). The existing bundled sidecars were left untouched."
}

$ResolvedRoot = [System.IO.Path]::GetFullPath($Root).TrimEnd([System.IO.Path]::DirectorySeparatorChar)
$ResolvedResourceBackend = [System.IO.Path]::GetFullPath($ResourceBackend)
if (-not $ResolvedResourceBackend.StartsWith($ResolvedRoot + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) {
  throw "Refusing to clean backend resources outside the workspace: $ResolvedResourceBackend"
}
$RequiredExecutables = @(
  "mimir-core-v2-scan.exe",
  "mimir-core-v2-ai-enrich.exe",
  "mimir-core-v2-actions.exe",
  "mimir-core-v2-dataset.exe",
  "mimir-core-v2-model-update.exe"
)

# Confirm every replacement exists BEFORE removing what is already bundled.
# Previously the wipe came first and the check second, so a build that failed
# to produce one executable left the app with no sidecars at all -- broken
# until the next successful build, from a command whose only visible symptom
# was an error about a missing file.
foreach ($ExecutableName in $RequiredExecutables) {
  $Source = Join-Path $BackendDist $ExecutableName
  if (!(Test-Path $Source)) {
    throw "Expected backend executable was not built: $Source. The existing bundled sidecars were left untouched."
  }
}

if (Test-Path -LiteralPath $ResolvedResourceBackend) {
  Get-ChildItem -LiteralPath $ResolvedResourceBackend -Force | Remove-Item -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $ResolvedResourceBackend | Out-Null

foreach ($ExecutableName in $RequiredExecutables) {
  Copy-Item -Force (Join-Path $BackendDist $ExecutableName) (Join-Path $ResolvedResourceBackend $ExecutableName)
}

$AgePackageRoot = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages\FiloSottile.age_Microsoft.Winget.Source_8wekyb3d8bbwe\age"
$AgeExecutable = Join-Path $AgePackageRoot "age.exe"
$AgeLicense = Join-Path $AgePackageRoot "LICENSE"
if (-not (Test-Path -LiteralPath $AgeExecutable) -or -not (Test-Path -LiteralPath $AgeLicense)) {
  throw "Official age 1.3.1 is required. Install with: winget install --id FiloSottile.age --exact"
}
$AgeVersion = (& $AgeExecutable --version).Trim()
if ($AgeVersion -ne "v1.3.1" -and $AgeVersion -ne "1.3.1") {
  throw "Pinned age version mismatch. Expected 1.3.1, found $AgeVersion."
}
Copy-Item -Force -LiteralPath $AgeExecutable -Destination (Join-Path $ResolvedResourceBackend "age.exe")
Copy-Item -Force -LiteralPath $AgeLicense -Destination (Join-Path $ResolvedResourceBackend "AGE-LICENSE.txt")

Write-Host "Built Core v2 backend resources: $ResolvedResourceBackend"
