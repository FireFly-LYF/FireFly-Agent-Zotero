#!/usr/bin/env bash
# Start FireFly Zotero bridge (HTTP 127.0.0.1:8765).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="${FIREFLY_INSTALL_DIR:-$HOME/FireFly-Agent-Zotero}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --install-dir) INSTALL_DIR="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: start-bridge.sh [--install-dir DIR]"
      exit 0
      ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

VENV="$INSTALL_DIR/venv"
CONFIG="$INSTALL_DIR/config/config.json"
WORKSPACE="$INSTALL_DIR/workspace"

if [[ ! -x "$VENV/bin/firefly" ]]; then
  echo "Error: FireFly not installed at $INSTALL_DIR. Run install-backend.sh first." >&2
  exit 1
fi
if [[ ! -f "$CONFIG" ]]; then
  echo "Error: config not found: $CONFIG" >&2
  exit 1
fi

# shellcheck disable=SC1091
source "$VENV/bin/activate"
echo "Starting FireFly Zotero bridge (config=$CONFIG)"
exec firefly zotero --config "$CONFIG" --workspace "$WORKSPACE"
