param(
  [Parameter(Mandatory = $true)][ValidateSet("windows_10", "windows_11")][string]$Platform,
  [Parameter(Mandatory = $true)][ValidateSet("install", "scan", "upgrade", "rollback", "uninstall")][string]$Stage,
  [Parameter(Mandatory = $true)][bool]$Passed,
  [Parameter(Mandatory = $true)][string]$Artifact,
  [string]$Notes = "",
  [string]$Output = ""
)

$Root = Split-Path -Parent $PSScriptRoot
$Artifact = [System.IO.Path]::GetFullPath($Artifact)
if (-not (Test-Path -LiteralPath $Artifact -PathType Leaf)) { throw "Release artifact missing: $Artifact" }
$OperatingSystem = Get-CimInstance Win32_OperatingSystem
$ExpectedCaption = if ($Platform -eq "windows_10") { "Windows 10" } else { "Windows 11" }
$OsMatches = $OperatingSystem.Caption -like "*$ExpectedCaption*"
$PythonOnPath = [bool](Get-Command python -ErrorAction SilentlyContinue)
$SignatureStatus = (Get-AuthenticodeSignature -LiteralPath $Artifact).Status.ToString()
if ($Passed -and -not $OsMatches) { throw "This machine is not the requested clean VM platform: $ExpectedCaption" }
if ($Passed -and $PythonOnPath) { throw "Clean-VM evidence requires a machine without Python on PATH." }
if ($Passed -and $SignatureStatus -ne "Valid") { throw "Clean-VM evidence requires a validly signed artifact." }
if ([string]::IsNullOrWhiteSpace($Output)) {
  $Output = Join-Path $Root "release_assets\clean_vm_report.json"
}
$Report = if (Test-Path -LiteralPath $Output) {
  Get-Content -Raw -LiteralPath $Output | ConvertFrom-Json
} else {
  [pscustomobject]@{ schema_version = "mimir_clean_vm_report_v1"; checks = [pscustomobject]@{} }
}
if (-not $Report.checks.PSObject.Properties[$Platform]) {
  $Report.checks | Add-Member -NotePropertyName $Platform -NotePropertyValue ([pscustomobject]@{})
}
$PlatformChecks = $Report.checks.PSObject.Properties[$Platform].Value
$StageValue = [pscustomobject]@{
  passed = $Passed
  recorded_at = (Get-Date).ToUniversalTime().ToString("o")
  machine = $env:COMPUTERNAME
  os = $OperatingSystem.Caption
  os_version = $OperatingSystem.Version
  os_matches_requested = $OsMatches
  python_on_path = $PythonOnPath
  artifact = $Artifact
  artifact_sha256 = (Get-FileHash -LiteralPath $Artifact -Algorithm SHA256).Hash.ToLowerInvariant()
  authenticode_status = $SignatureStatus
  notes = $Notes
}
$PlatformChecks | Add-Member -NotePropertyName $Stage -NotePropertyValue $StageValue -Force
$Required = @("install", "scan", "upgrade", "rollback", "uninstall")
foreach ($Name in @("windows_10", "windows_11")) {
  $Complete = $true
  $NamedChecks = if ($Report.checks.PSObject.Properties[$Name]) { $Report.checks.PSObject.Properties[$Name].Value } else { $null }
  foreach ($RequiredStage in $Required) {
    if (-not $NamedChecks -or -not $NamedChecks.PSObject.Properties[$RequiredStage] -or -not $NamedChecks.PSObject.Properties[$RequiredStage].Value.passed) {
      $Complete = $false
    }
  }
  $Report | Add-Member -NotePropertyName "${Name}_passed" -NotePropertyValue $Complete -Force
}
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Output) | Out-Null
$Report | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $Output -Encoding UTF8
Write-Host "Clean VM evidence updated: $Output"
