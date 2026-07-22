param(
  [string]$CertificateThumbprint = $env:MIMIR_SIGNING_CERT_THUMBPRINT,
  [string]$TimestampUrl = "http://timestamp.digicert.com",
  [string]$UpdaterPrivateKey = "C:\Mimir_Data\keys\mimir-updater.key",
  [string]$UpdaterPasswordFile = "C:\Mimir_Data\keys\mimir-updater-password.txt",
  [string]$UpdaterEndpoint = $env:MIMIR_UPDATE_ENDPOINT
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$BackendResources = Join-Path $Root "src-tauri\resources\mimir-backend"
$BundleRoot = Join-Path $Root "src-tauri\target\release\bundle"
$ReleaseConfig = Join-Path $Root "src-tauri\tauri.release-signing.conf.json"

function Find-SignTool {
  $Candidates = Get-ChildItem "C:\Program Files (x86)\Windows Kits\10\bin" -Recurse -Filter signtool.exe -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -match "\\x64\\signtool\.exe$" } |
    Sort-Object FullName -Descending
  if (-not $Candidates) {
    throw "Windows SDK SignTool was not found."
  }
  return $Candidates[0].FullName
}

function Assert-TrustedCertificate([string]$Thumbprint) {
  if ([string]::IsNullOrWhiteSpace($Thumbprint)) {
    throw "MIMIR_SIGNING_CERT_THUMBPRINT is required for an external beta build."
  }
  $Certificate = Get-ChildItem Cert:\CurrentUser\My, Cert:\LocalMachine\My -ErrorAction SilentlyContinue |
    Where-Object { $_.Thumbprint -eq $Thumbprint } |
    Select-Object -First 1
  if (-not $Certificate) {
    throw "The requested code-signing certificate is not installed."
  }
  if (-not $Certificate.HasPrivateKey) {
    throw "The code-signing certificate has no accessible private key."
  }
  if ($Certificate.NotAfter -le (Get-Date)) {
    throw "The code-signing certificate has expired."
  }
}

function Sign-And-Verify([string]$Path, [string]$SignTool) {
  if (-not (Test-Path -LiteralPath $Path)) {
    throw "Release artifact is missing: $Path"
  }
  & $SignTool sign /sha1 $CertificateThumbprint /fd SHA256 /tr $TimestampUrl /td SHA256 $Path
  if ($LASTEXITCODE -ne 0) { throw "SignTool failed for $Path" }
  & $SignTool verify /pa /all /v $Path | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "Signature verification failed for $Path" }
}

Assert-TrustedCertificate $CertificateThumbprint
if ([string]::IsNullOrWhiteSpace($UpdaterEndpoint) -or -not $UpdaterEndpoint.StartsWith("https://")) {
  throw "MIMIR_UPDATE_ENDPOINT must be an HTTPS manifest URL for an external beta build."
}
if (-not (Test-Path -LiteralPath $UpdaterPrivateKey) -or -not (Test-Path -LiteralPath $UpdaterPasswordFile)) {
  throw "The offline Tauri updater signing key or password file is missing."
}
$SignTool = Find-SignTool

Push-Location $Root
try {
  npm test
  if ($LASTEXITCODE -ne 0) { throw "Frontend tests failed." }
  npm run type-check
  if ($LASTEXITCODE -ne 0) { throw "Type checking failed." }
  npm run sidecar:build
  if ($LASTEXITCODE -ne 0) { throw "Sidecar build failed." }

  foreach ($Name in @("mimir-core-v2-scan.exe", "mimir-core-v2-actions.exe", "mimir-core-v2-dataset.exe", "mimir-core-v2-release-check.exe", "age.exe")) {
    Sign-And-Verify (Join-Path $BackendResources $Name) $SignTool
  }

  $Config = @{
    bundle = @{
      windows = @{
        certificateThumbprint = $CertificateThumbprint
        digestAlgorithm = "sha256"
        timestampUrl = $TimestampUrl
      }
    }
    plugins = @{
      updater = @{
        endpoints = @($UpdaterEndpoint)
      }
    }
  }
  $Config | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $ReleaseConfig -Encoding UTF8
  $env:TAURI_SIGNING_PRIVATE_KEY = $UpdaterPrivateKey
  $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = (Get-Content -Raw -LiteralPath $UpdaterPasswordFile).Trim()
  npm run tauri build -- --config $ReleaseConfig
  if ($LASTEXITCODE -ne 0) { throw "Signed Tauri build failed." }

  $Artifacts = Get-ChildItem -Path $BundleRoot -Recurse -File |
    Where-Object { $_.Extension -in @(".exe", ".msi") }
  if (-not $Artifacts) { throw "No Windows release artifacts were produced." }
  foreach ($Artifact in $Artifacts) {
    & $SignTool verify /pa /all /v $Artifact.FullName | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Signature verification failed for $($Artifact.FullName)" }
  }
  $Signatures = Get-ChildItem -Path $BundleRoot -Recurse -File -Filter *.sig
  if (-not $Signatures) { throw "No signed updater artifact was produced." }
} finally {
  Remove-Item -LiteralPath $ReleaseConfig -Force -ErrorAction SilentlyContinue
  Remove-Item Env:\TAURI_SIGNING_PRIVATE_KEY -ErrorAction SilentlyContinue
  Remove-Item Env:\TAURI_SIGNING_PRIVATE_KEY_PASSWORD -ErrorAction SilentlyContinue
  Pop-Location
}

Write-Host "SIGNED RELEASE ARTIFACTS VERIFIED" -ForegroundColor Green
