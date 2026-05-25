#!/usr/bin/env bash
# Install FireFly backend from GitHub Release assets (wheel + llm-wiki template).
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: install-backend.sh [options]

Options:
  --install-dir DIR   Install root (default: ~/FireFly-Agent-Zotero)
  --wheel PATH        firefly_ai-*.whl (default: auto-detect next to this script)
  --template PATH     llm-wiki-template.zip (default: auto-detect)
  --python PATH       Python 3.11+ executable (default: python3)
  -h, --help          Show help
EOF
}

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="${FIREFLY_INSTALL_DIR:-$HOME/FireFly-Agent-Zotero}"
WHEEL=""
TEMPLATE=""
PYTHON="${PYTHON:-python3}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --install-dir) INSTALL_DIR="$2"; shift 2 ;;
    --wheel) WHEEL="$2"; shift 2 ;;
    --template) TEMPLATE="$2"; shift 2 ;;
    --python) PYTHON="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

if [[ -z "$WHEEL" ]]; then
  WHEEL="$(find "$SCRIPT_DIR" -maxdepth 1 -name 'firefly_ai-*.whl' | head -n 1 || true)"
fi
if [[ -z "$TEMPLATE" ]]; then
  TEMPLATE="$(find "$SCRIPT_DIR" -maxdepth 1 -name 'llm-wiki-template.zip' | head -n 1 || true)"
fi

if [[ -z "$WHEEL" || ! -f "$WHEEL" ]]; then
  echo "Error: firefly_ai-*.whl not found. Pass --wheel or run from Release download folder." >&2
  exit 1
fi
if [[ -z "$TEMPLATE" || ! -f "$TEMPLATE" ]]; then
  echo "Error: llm-wiki-template.zip not found. Pass --template or run from Release download folder." >&2
  exit 1
fi

"$PYTHON" - <<'PY' >/dev/null
import sys
if sys.version_info < (3, 11):
    raise SystemExit(f"Python 3.11+ required, got {sys.version}")
PY

mkdir -p "$INSTALL_DIR"
VENV="$INSTALL_DIR/venv"
CONFIG_DIR="$INSTALL_DIR/config"
WORKSPACE="$INSTALL_DIR/workspace"
LLM_WIKI="$INSTALL_DIR/llm-wiki"

if [[ ! -d "$VENV" ]]; then
  echo "Creating venv at $VENV"
  "$PYTHON" -m venv "$VENV"
fi

# shellcheck disable=SC1091
source "$VENV/bin/activate"
python -m pip install --upgrade pip wheel
pip install "$WHEEL[api,pdf]"

mkdir -p "$CONFIG_DIR" "$WORKSPACE"
if [[ ! -f "$LLM_WIKI/AGENTS.md" ]]; then
  echo "Extracting llm-wiki template to $LLM_WIKI"
  mkdir -p "$LLM_WIKI"
  unzip -qo "$TEMPLATE" -d "$LLM_WIKI"
fi

CONFIG="$CONFIG_DIR/config.json"
if [[ ! -f "$CONFIG" ]]; then
  echo "Initializing default config at $CONFIG"
  firefly onboard --config "$CONFIG" --workspace "$WORKSPACE" </dev/null || true
fi

cat <<EOF

FireFly backend installed.

  Install dir : $INSTALL_DIR
  Config      : $CONFIG
  Workspace   : $WORKSPACE
  llm-wiki    : $LLM_WIKI

Next steps:
  1. Edit $CONFIG and add your LLM API key
  2. Start bridge: $SCRIPT_DIR/start-bridge.sh --install-dir "$INSTALL_DIR"
  3. Install the .xpi plugin in Zotero

EOF
