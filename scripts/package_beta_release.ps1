$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Package = Get-Content (Join-Path $Root "package.json") -Raw | ConvertFrom-Json
$ReleaseName = "Mimir-Free-Private-Beta-v$($Package.version)"
$ReleaseRoot = Join-Path $Root "dist-release"
$ReleaseDir = Join-Path $ReleaseRoot $ReleaseName
$BundleDir = Join-Path $Root "src-tauri\target\release\bundle"
$AssetsDir = Join-Path $Root "release_assets"

function Write-Step($Message) {
  Write-Host ""
  Write-Host "== $Message ==" -ForegroundColor Cyan
}

function Find-Installer {
  $Nsis = Get-ChildItem -Path (Join-Path $BundleDir "nsis") -Filter "*.exe" -File -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1

  if ($Nsis) {
    return $Nsis
  }

  $Msi = Get-ChildItem -Path (Join-Path $BundleDir "msi") -Filter "*.msi" -File -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1

  if ($Msi) {
    return $Msi
  }

  throw "No Windows installer was found under $BundleDir"
}

Write-Step "Building frontend"
Push-Location $Root
try {
  Write-Step "Running desktop unit and type checks"
  npm test
  npm run type-check

  Write-Step "Running desktop shell tests"
  Push-Location (Join-Path $Root "src-tauri")
  try {
    cargo test
  }
  finally {
    Pop-Location
  }

  Write-Step "Building self-contained Core v2 executables"
  npm run sidecar:build

  Write-Step "Generating software bill of materials"
  npm run sbom

  Write-Step "Running dependency, secret, and packaged-content audit"
  npm run security:audit
  if ($LASTEXITCODE -ne 0) {
    throw "Release security audit failed. See release_assets\security_scan_report.json."
  }

  npm run build

  Write-Step "Building Tauri Windows installer"
  npm run tauri build
}
finally {
  Pop-Location
}

Write-Step "Running strict free-beta release gate"
$BackendRoot = $env:MIMIR_BACKEND_ROOT
if ([string]::IsNullOrWhiteSpace($BackendRoot)) {
  $BackendRoot = Join-Path (Split-Path -Parent $Root) "Mimir_Backend"
}
$BackendRoot = [System.IO.Path]::GetFullPath($BackendRoot)
$ReleaseCheck = Join-Path $BackendRoot "mimir_core_v2_release_check.py"
$BackendPython = Join-Path $BackendRoot ".venv\Scripts\python.exe"
& $BackendPython $ReleaseCheck --gate-only --frontend-root $Root
if ($LASTEXITCODE -ne 0) {
  throw "Strict release gate failed. This installer must not be distributed."
}

Write-Step "Creating beta release folder"
$Installer = Find-Installer

if (Test-Path $ReleaseDir) {
  Remove-Item -Recurse -Force $ReleaseDir
}

New-Item -ItemType Directory -Force -Path $ReleaseDir | Out-Null

$InstallerName = if ($Installer.Extension -ieq ".exe") { "MimirSetup.exe" } else { "MimirInstaller.msi" }
Copy-Item -Force $Installer.FullName (Join-Path $ReleaseDir $InstallerName)

Copy-Item -Force (Join-Path $AssetsDir "README_START_HERE.html") (Join-Path $ReleaseDir "README_START_HERE.html")
Copy-Item -Force (Join-Path $AssetsDir "PRIVATE_BETA_TESTING.md") (Join-Path $ReleaseDir "PRIVATE_BETA_TESTING.md")
Copy-Item -Force (Join-Path $AssetsDir "sbom.cdx.json") (Join-Path $ReleaseDir "sbom.cdx.json")
Copy-Item -Recurse -Force (Join-Path $Root "docs") (Join-Path $ReleaseDir "docs")

$ScreenshotCandidates = @(
  (Join-Path $AssetsDir "screenshot.png"),
  (Join-Path $AssetsDir "screenshot.jpg"),
  (Join-Path $AssetsDir "mimir-screenshot.png"),
  (Join-Path $Root "screenshot.png")
)

foreach ($Candidate in $ScreenshotCandidates) {
  if (Test-Path $Candidate) {
    Copy-Item -Force $Candidate (Join-Path $ReleaseDir (Split-Path -Leaf $Candidate))
    break
  }
}

Write-Host ""
Write-Host "Private beta release package created:" -ForegroundColor Green
Write-Host $ReleaseDir
Write-Host ""
Write-Host "Contents:"
Get-ChildItem $ReleaseDir | Select-Object Name, Length, LastWriteTime | Format-Table -AutoSize
