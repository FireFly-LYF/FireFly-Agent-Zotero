# Install FireFly backend from the release bundle (Windows).
param(
    [string]$InstallDir = $(if ($env:FIREFLY_INSTALL_DIR) { $env:FIREFLY_INSTALL_DIR } else { Join-Path $env:USERPROFILE "FireFly-Agent-Zotero" }),
    [string]$WheelPath = "",
    [string]$LlmWikiSource = "",
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

function Find-File([string]$Pattern, [string[]]$Dirs) {
    foreach ($dir in $Dirs) {
        if (-not (Test-Path $dir)) { continue }
        $hit = Get-ChildItem -Path $dir -Filter $Pattern -File -Recurse -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($hit) { return $hit.FullName }
    }
    return ""
}

$SearchDirs = @(
    $ScriptDir,
    (Join-Path $ScriptDir "backend"),
    (Join-Path $ScriptDir "plugin")
)

if (-not $WheelPath) {
    $WheelPath = Find-File "firefly_ai-*.whl" $SearchDirs
}
if (-not $LlmWikiSource) {
    $wikiDir = Join-Path $ScriptDir "llm-wiki"
    if (Test-Path (Join-Path $wikiDir "AGENTS.md")) {
        $LlmWikiSource = $wikiDir
    } else {
        $LlmWikiSource = Find-File "llm-wiki-template.zip" $SearchDirs
    }
}

if (-not $WheelPath -or -not (Test-Path $WheelPath)) {
    throw "firefly_ai-*.whl not found. Unzip the release bundle and run this script from its folder."
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
    Write-Host "Installing llm-wiki template to $LlmWiki"
    New-Item -ItemType Directory -Force -Path $LlmWiki | Out-Null
    if ((Test-Path $LlmWikiSource) -and (Get-Item $LlmWikiSource).PSIsContainer) {
        Copy-Item -Path (Join-Path $LlmWikiSource "*") -Destination $LlmWiki -Recurse -Force
    } elseif (Test-Path $LlmWikiSource) {
        Expand-Archive -Path $LlmWikiSource -DestinationPath $LlmWiki -Force
    } else {
        throw "llm-wiki template not found in bundle."
    }
}

if (-not (Test-Path $Config)) {
    Write-Host "Initializing default config at $Config"
    & $Firefly onboard --config $Config --workspace $Workspace 2>$null
}

$XpiPath = Find-File "fire-fly-agent-zotero.xpi" $SearchDirs

Write-Host @"

FireFly backend installed.

  Install dir : $InstallDir
  Config      : $Config
  Workspace   : $Workspace
  llm-wiki    : $LlmWiki

Next steps:
  1. Edit $Config and add your LLM API key
  2. Start bridge: $ScriptDir\start-bridge.ps1 -InstallDir "$InstallDir"
  3. Install plugin in Zotero: Tools -> Plugins -> Install from file
$(if ($XpiPath) { "     XPI: $XpiPath" } else { "" })

"@
