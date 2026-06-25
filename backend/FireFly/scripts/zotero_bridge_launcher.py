#!/usr/bin/env python3
"""Start FireFly Zotero bridge.

Development (monorepo clone):
  python backend/FireFly/scripts/zotero_bridge_launcher.py

Release install:
  %USERPROFILE%\\FireFly-Agent-Zotero\\release\\start-bridge.ps1
  or start-bridge.ps1
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    # backend/FireFly/scripts/ -> repo root
    return here.parents[3]


def _load_context_config() -> tuple[Path, Path] | None:
    root = _repo_root()
    ctx_path = root / "backend" / "config" / "context.json"
    if not ctx_path.is_file():
        return None
    try:
        data = json.loads(ctx_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    cfg = str(data.get("config_path", "")).strip()
    ws = str(data.get("workspace", "")).strip()
    if not cfg or not ws:
        return None
    return Path(cfg).expanduser().resolve(), Path(ws).expanduser().resolve()


def main() -> int:
    env_cfg = os.environ.get("FIREFLY_CONFIG", "").strip()
    env_ws = os.environ.get("FIREFLY_WORKSPACE", "").strip()
    if env_cfg and env_ws:
        config_path = Path(env_cfg).expanduser().resolve()
        workspace = Path(env_ws).expanduser().resolve()
    else:
        loaded = _load_context_config()
        if loaded is None:
            print(
                "Cannot resolve config/workspace.\n"
                "  - Set FIREFLY_CONFIG and FIREFLY_WORKSPACE, or\n"
                "  - Create backend/config/context.json (dev clone), or\n"
                "  - Use release/start-bridge.ps1 after install-backend.",
                file=sys.stderr,
            )
            return 1
        config_path, workspace = loaded

    if not config_path.is_file():
        print(f"Config not found: {config_path}", file=sys.stderr)
        return 1

    cli_args = [
        "zotero",
        "agent",
        "--config",
        str(config_path),
        "--workspace",
        str(workspace),
    ]

    # Use the current interpreter. Windows entry-point scripts (firefly.exe)
    # break when a conda env is moved or recreated.
    try:
        from firefly.cli.commands import app

        app(
            cli_args,
            prog_name="firefly zotero",
            standalone_mode=False,
        )
        return 0
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1


if __name__ == "__main__":
    raise SystemExit(main())
