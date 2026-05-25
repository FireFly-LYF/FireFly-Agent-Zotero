# Start FireFly Zotero bridge (HTTP 127.0.0.1:8765).
param(
    [string]$InstallDir = $(if ($env:FIREFLY_INSTALL_DIR) { $env:FIREFLY_INSTALL_DIR } else { Join-Path $env:USERPROFILE "FireFly-Agent-Zotero" })
)

$ErrorActionPreference = "Stop"
$Venv = Join-Path $InstallDir "venv"
$Firefly = Join-Path $Venv "Scripts\firefly.exe"
$Config = Join-Path $InstallDir "config\config.json"
$Workspace = Join-Path $InstallDir "workspace"

if (-not (Test-Path $Firefly)) {
    throw "FireFly not installed at $InstallDir. Run install-backend.ps1 first."
}
if (-not (Test-Path $Config)) {
    throw "Config not found: $Config"
}

Write-Host "Starting FireFly Zotero bridge (config=$Config)"
& $Firefly zotero --config $Config --workspace $Workspace
