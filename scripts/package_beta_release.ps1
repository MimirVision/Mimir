$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$ReleaseName = "Mimir-Beta-v0.1"
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
  npm run build

  Write-Step "Building Tauri Windows installer"
  npm run tauri build
}
finally {
  Pop-Location
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
