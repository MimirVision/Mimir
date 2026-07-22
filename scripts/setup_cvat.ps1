param(
    [string]$InstallRoot = "C:\Mimir_Data\cvat",
    [string]$Version = "v2.70.0",
    [string]$Commit = "f65b33bc09753ea72daf2cb71d2653068f9d3b08"
)

$ErrorActionPreference = "Stop"
$Target = Join-Path $InstallRoot $Version

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker Desktop is required for the local CVAT Community deployment."
}

New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
if (-not (Test-Path (Join-Path $Target ".git"))) {
    git clone --branch $Version --depth 1 https://github.com/cvat-ai/cvat.git $Target
}

Push-Location $Target
try {
    $ActualCommit = (git rev-parse HEAD).Trim()
    if ($ActualCommit -ne $Commit) {
        throw "Pinned CVAT commit mismatch. Expected $Commit, found $ActualCommit."
    }
    $env:CVAT_HOST = "localhost"
    docker compose up -d
    if ($LASTEXITCODE -ne 0) {
        throw "CVAT Docker deployment failed."
    }
} finally {
    Pop-Location
}

Write-Host "CVAT Community $Version is running at http://localhost:8080"
Write-Host "Create credentials locally; never commit passwords or API tokens."
