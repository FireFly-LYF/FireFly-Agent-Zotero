# Install FireFly backend from GitHub Release assets (wheel + llm-wiki template).
param(
    [string]$InstallDir = $(if ($env:FIREFLY_INSTALL_DIR) { $env:FIREFLY_INSTALL_DIR } else { Join-Path $env:USERPROFILE "FireFly-Agent-Zotero" }),
    [string]$WheelPath = "",
    [string]$TemplateZip = "",
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

function Find-Asset([string]$Pattern) {
    Get-ChildItem -Path $ScriptDir -Filter $Pattern -File -ErrorAction SilentlyContinue |
        Select-Object -First 1 -ExpandProperty FullName
}

if (-not $WheelPath) { $WheelPath = Find-Asset "firefly_ai-*.whl" }
if (-not $TemplateZip) { $TemplateZip = Find-Asset "llm-wiki-template.zip" }

if (-not $WheelPath -or -not (Test-Path $WheelPath)) {
    throw "firefly_ai-*.whl not found. Pass -WheelPath or run from Release download folder."
}
if (-not $TemplateZip -or -not (Test-Path $TemplateZip)) {
    throw "llm-wiki-template.zip not found. Pass -TemplateZip or run from Release download folder."
}

& $Python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
if ($LASTEXITCODE -ne 0) { throw "Python 3.11+ required." }

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
$Venv = Join-Path $InstallDir "venv"
$ConfigDir = Join-Path $InstallDir "config"
$Workspace = Join-Path $InstallDir "workspace"
$LlmWiki = Join-Path $InstallDir "llm-wiki"
$Config = Join-Path $ConfigDir "config.json"

if (-not (Test-Path $Venv)) {
    Write-Host "Creating venv at $Venv"
    & $Python -m venv $Venv
}

$Py = Join-Path $Venv "Scripts\python.exe"
$Firefly = Join-Path $Venv "Scripts\firefly.exe"

& $Py -m pip install --upgrade pip wheel
& $Py -m pip install "${WheelPath}[api,pdf]"

New-Item -ItemType Directory -Force -Path $ConfigDir, $Workspace | Out-Null

if (-not (Test-Path (Join-Path $LlmWiki "AGENTS.md"))) {
    Write-Host "Extracting llm-wiki template to $LlmWiki"
    New-Item -ItemType Directory -Force -Path $LlmWiki | Out-Null
    Expand-Archive -Path $TemplateZip -DestinationPath $LlmWiki -Force
}

if (-not (Test-Path $Config)) {
    Write-Host "Initializing default config at $Config"
    & $Firefly onboard --config $Config --workspace $Workspace 2>$null
}

Write-Host @"

FireFly backend installed.

  Install dir : $InstallDir
  Config      : $Config
  Workspace   : $Workspace
  llm-wiki    : $LlmWiki

Next steps:
  1. Edit $Config and add your LLM API key
  2. Start bridge: $ScriptDir\start-bridge.ps1 -InstallDir "$InstallDir"
  3. Install the .xpi plugin in Zotero

"@
