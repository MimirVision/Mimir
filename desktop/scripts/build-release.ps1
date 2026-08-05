<#
  Builds the distributable installer with updater artifacts signed.

  `npm run desktop:build` fails on its own with:

      A public key has been found, but no private key. Make sure to set
      `TAURI_SIGNING_PRIVATE_KEY` environment variable.

  because tauri.conf.json pins an updater pubkey and sets
  createUpdaterArtifacts, so Tauri insists on signing the update manifest.
  The key is not in the repo and never should be, so it has to be handed in
  through the environment -- which is easy to forget and gives an error that
  does not say where the key lives.

  Defaults point at the developer key location. Override with
  MIMIR_UPDATER_KEY / MIMIR_UPDATER_PASSWORD_FILE, or set
  TAURI_SIGNING_PRIVATE_KEY yourself and this gets out of the way.

  For a throwaway build that needs no key at all, use
  `npm run desktop:build:internal` instead -- but note RELEASE_READINESS.md:
  an internal build must never be handed out as an external beta release,
  because it produces no updater artifacts, so anyone installing it can
  never be updated.
#>

param(
  [string]$KeyPath = $(if ($env:MIMIR_UPDATER_KEY) { $env:MIMIR_UPDATER_KEY } else { "C:\Mimir_Data\keys\mimir-updater.key" }),
  [string]$PasswordFile = $(if ($env:MIMIR_UPDATER_PASSWORD_FILE) { $env:MIMIR_UPDATER_PASSWORD_FILE } else { "C:\Mimir_Data\keys\mimir-updater-password.txt" })
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

if (-not $env:TAURI_SIGNING_PRIVATE_KEY) {
  if (-not (Test-Path -LiteralPath $KeyPath)) {
    throw @"
Updater signing key not found: $KeyPath

Either point MIMIR_UPDATER_KEY at it, set TAURI_SIGNING_PRIVATE_KEY yourself,
or build without updater artifacts using:  npm run desktop:build:internal
"@
  }
  # Tauri accepts the key material itself, not a path.
  $env:TAURI_SIGNING_PRIVATE_KEY = (Get-Content -LiteralPath $KeyPath -Raw).Trim()
  Write-Host "Loaded updater signing key from $KeyPath"
}

if (-not $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD) {
  if (Test-Path -LiteralPath $PasswordFile) {
    $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = (Get-Content -LiteralPath $PasswordFile -Raw).Trim()
    Write-Host "Loaded updater key password from $PasswordFile"
  } else {
    # An unencrypted key is valid; Tauri only prompts when one is needed.
    $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = ""
  }
}

Push-Location $Root
try {
  & powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "build-sidecar.ps1")
  if ($LASTEXITCODE -ne 0) { throw "Sidecar build failed (exit $LASTEXITCODE)." }

  & node ./node_modules/@tauri-apps/cli/tauri.js build
  if ($LASTEXITCODE -ne 0) { throw "Tauri build failed (exit $LASTEXITCODE)." }
}
finally {
  Pop-Location
  # Do not leave key material in the session's environment.
  Remove-Item Env:\TAURI_SIGNING_PRIVATE_KEY -ErrorAction SilentlyContinue
  Remove-Item Env:\TAURI_SIGNING_PRIVATE_KEY_PASSWORD -ErrorAction SilentlyContinue
}

$Bundle = Join-Path $Root "src-tauri\target\release\bundle\nsis"
$Installer = Get-ChildItem -LiteralPath $Bundle -Filter "*_x64-setup.exe" -ErrorAction SilentlyContinue |
  Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($Installer) {
  $Sha = (Get-FileHash $Installer.FullName -Algorithm SHA256).Hash
  Write-Host ""
  Write-Host "Installer : $($Installer.FullName)"
  Write-Host ("Size      : {0:N1} MB" -f ($Installer.Length / 1MB))
  Write-Host "SHA-256   : $Sha"
} else {
  Write-Warning "Build reported success but no installer was found in $Bundle"
}
