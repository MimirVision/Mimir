param(
  [Parameter(Mandatory = $true)][string]$PreviousInstaller,
  [Parameter(Mandatory = $true)][string]$CandidateInstaller,
  [Parameter(Mandatory = $true)][string]$SessionRoot,
  [string]$Output = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($Output)) {
  $Output = Join-Path $Root "release_assets\update_rollback_report.json"
}

foreach ($Path in @($PreviousInstaller, $CandidateInstaller)) {
  if (-not (Test-Path -LiteralPath $Path)) { throw "Installer missing: $Path" }
  if ((Get-AuthenticodeSignature -LiteralPath $Path).Status -ne "Valid") {
    throw "Installer is not validly signed: $Path"
  }
}

$Before = @{}
Get-ChildItem -LiteralPath $SessionRoot -Recurse -File -ErrorAction SilentlyContinue | ForEach-Object {
  $Before[$_.FullName] = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
}

$Candidate = Start-Process -FilePath $CandidateInstaller -ArgumentList "/S" -Wait -PassThru
$CandidatePassed = $Candidate.ExitCode -eq 0
$Previous = Start-Process -FilePath $PreviousInstaller -ArgumentList "/S" -Wait -PassThru
$RollbackPassed = $Previous.ExitCode -eq 0

$CorruptUpdateRejected = $false
$FailedUpdateRecoveryPassed = $false
$CorruptCandidate = Join-Path $env:TEMP ("mimir-corrupt-update-" + [guid]::NewGuid().ToString("N") + ".exe")
try {
  Copy-Item -LiteralPath $CandidateInstaller -Destination $CorruptCandidate
  $Stream = [System.IO.File]::Open($CorruptCandidate, [System.IO.FileMode]::Open, [System.IO.FileAccess]::ReadWrite)
  try {
    if ($Stream.Length -lt 1024) { throw "Candidate installer is unexpectedly small." }
    $Stream.Position = [Math]::Floor($Stream.Length / 2)
    $Original = $Stream.ReadByte()
    $Stream.Position--
    $Stream.WriteByte(($Original -bxor 0xFF))
  }
  finally {
    $Stream.Dispose()
  }
  $CorruptUpdateRejected = (Get-AuthenticodeSignature -LiteralPath $CorruptCandidate).Status -ne "Valid"
  $Recovery = Start-Process -FilePath $PreviousInstaller -ArgumentList "/S" -Wait -PassThru
  $FailedUpdateRecoveryPassed = $CorruptUpdateRejected -and $Recovery.ExitCode -eq 0
}
finally {
  Remove-Item -LiteralPath $CorruptCandidate -Force -ErrorAction SilentlyContinue
}

$After = @{}
Get-ChildItem -LiteralPath $SessionRoot -Recurse -File -ErrorAction SilentlyContinue | ForEach-Object {
  $After[$_.FullName] = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
}
$BeforeEntries = @($Before.GetEnumerator() | Sort-Object Key | ForEach-Object { "$($_.Key)=$($_.Value)" })
$AfterEntries = @($After.GetEnumerator() | Sort-Object Key | ForEach-Object { "$($_.Key)=$($_.Value)" })
$SessionsPreserved = ($Before.Count -eq $After.Count) -and -not (Compare-Object $BeforeEntries $AfterEntries)

$Report = [ordered]@{
  schema_version = "mimir_update_rollback_report_v1"
  generated_at = (Get-Date).ToUniversalTime().ToString("o")
  previous_installer_sha256 = (Get-FileHash -LiteralPath $PreviousInstaller -Algorithm SHA256).Hash.ToLowerInvariant()
  candidate_installer_sha256 = (Get-FileHash -LiteralPath $CandidateInstaller -Algorithm SHA256).Hash.ToLowerInvariant()
  signed_update_passed = $CandidatePassed
  rollback_passed = $RollbackPassed
  corrupt_update_rejected = $CorruptUpdateRejected
  failed_update_recovery_passed = $FailedUpdateRecoveryPassed
  sessions_preserved = $SessionsPreserved
  summary = if ($CandidatePassed -and $RollbackPassed -and $CorruptUpdateRejected -and $FailedUpdateRecoveryPassed -and $SessionsPreserved) { "signed update, rollback, corrupt-package rejection, and session preservation passed" } else { "update or rollback evidence failed" }
}
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Output) | Out-Null
$Report | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $Output -Encoding UTF8
if (-not ($CandidatePassed -and $RollbackPassed -and $CorruptUpdateRejected -and $FailedUpdateRecoveryPassed -and $SessionsPreserved)) { exit 1 }
