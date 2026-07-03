$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"

if (Test-Path $VenvPython) {
    $Python = $VenvPython
} else {
    $Python = "python"
}

Write-Host "Mimir backend executable build"
Write-Host "Backend root: $Root"
Write-Host "Python: $Python"
Write-Host ""

& $Python -m PyInstaller --version | Out-Null

if ($LASTEXITCODE -ne 0) {
    Write-Host "PyInstaller is not available in this Python environment."
    Write-Host "Install it with: $Python -m pip install pyinstaller"
    exit 1
}

Write-Host "Compiling backend entrypoints..."
& $Python -m py_compile mimir_backend_cli.py
& $Python -m py_compile tesla_ai_sorter.py
& $Python -m py_compile mimir_clip_actions.py

Write-Host ""
Write-Host "Building dist\mimir-backend\mimir-backend.exe ..."
& $Python -m PyInstaller --noconfirm --clean .\mimir-backend.spec

if ($LASTEXITCODE -ne 0) {
    Write-Host "PyInstaller build failed."
    exit $LASTEXITCODE
}

$ExePath = Join-Path $Root "dist\mimir-backend\mimir-backend.exe"

if (-not (Test-Path $ExePath)) {
    Write-Host "Build finished but executable was not found: $ExePath"
    exit 1
}

Write-Host ""
Write-Host "Backend executable created:"
Write-Host $ExePath
