param(
  [Parameter(Mandatory = $true)][string]$Tester,
  [Parameter(Mandatory = $true)][string]$ScreenReader,
  [Parameter(Mandatory = $true)][string]$Scaling,
  [Parameter(Mandatory = $true)][string]$Notes,
  [string]$Output = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($Output)) {
  $Output = Join-Path $Root "release_assets\accessibility_report.json"
}
if (-not (Test-Path -LiteralPath $Output)) {
  throw "Accessibility report missing. Run npm run test:accessibility first."
}

$Report = Get-Content -Raw -LiteralPath $Output | ConvertFrom-Json
if (-not $Report.automated_passed) {
  throw "Manual evidence cannot override failed automated accessibility checks."
}
if ([string]::IsNullOrWhiteSpace($Tester) -or [string]::IsNullOrWhiteSpace($ScreenReader) -or [string]::IsNullOrWhiteSpace($Scaling) -or [string]::IsNullOrWhiteSpace($Notes)) {
  throw "Tester, screen reader, scaling, and notes are required."
}

$Report.checks.screen_reader_manual = $true
$Report.checks.scaling_manual = $true
$Report | Add-Member -NotePropertyName manual_desktop_passed -NotePropertyValue $true -Force
$Report | Add-Member -NotePropertyName passed -NotePropertyValue $true -Force
$Report | Add-Member -NotePropertyName summary -NotePropertyValue "automated and manual desktop accessibility checks passed" -Force
$Report | Add-Member -NotePropertyName manual_evidence -NotePropertyValue ([ordered]@{
  recorded_at = (Get-Date).ToUniversalTime().ToString("o")
  tester = $Tester
  screen_reader = $ScreenReader
  scaling = $Scaling
  notes = $Notes
}) -Force

$Report | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $Output -Encoding UTF8
Write-Host "Manual accessibility evidence recorded: $Output"
