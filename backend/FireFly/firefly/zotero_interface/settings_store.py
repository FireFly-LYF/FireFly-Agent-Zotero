"""Read/write FireFly config files for the Zotero plugin settings pane."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SETTING_KEYS = ("config", "context", "user")


def resolve_settings_paths() -> dict[str, Path]:
    from firefly.config.cli_prefs import cli_prefs_path_for
    from firefly.config.loader import _new_context_path, get_config_path, resolve_project_base_dir

    config_path = get_config_path()
    project_base = resolve_project_base_dir(config_path)
    return {
        "config": config_path,
        "context": _new_context_path(project_base),
        "user": cli_prefs_path_for(config_path),
    }


def _read_file_entry(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        return {
            "path": str(resolved),
            "exists": False,
            "content": "",
        }
    try:
        text = resolved.read_text(encoding="utf-8")
    except OSError as exc:
        return {
            "path": str(resolved),
            "exists": True,
            "content": "",
            "error": str(exc),
        }
    return {
        "path": str(resolved),
        "exists": True,
        "content": text,
    }


def read_settings_bundle() -> dict[str, Any]:
    files = {
        key: _read_file_entry(path)
        for key, path in resolve_settings_paths().items()
    }
    return {"ok": True, "files": files}


def _validate_setting(key: str, data: dict[str, Any]) -> str | None:
    if key == "config":
        from firefly.config.loader import _migrate_config
        from firefly.config.schema import Config

        try:
            Config.model_validate(_migrate_config(data))
        except Exception as exc:
            return f"config validation failed: {exc}"
    elif key == "context":
        for field in ("config_path", "workspace"):
            if field in data and not isinstance(data[field], str):
                return f"{field} must be a string"
    return None


def write_settings_bundle(payload: dict[str, Any]) -> dict[str, Any]:
    paths = resolve_settings_paths()
    saved: list[str] = []
    errors: dict[str, str] = {}
    restart_recommended = False

    for key in SETTING_KEYS:
        if key not in payload:
            continue
        raw = payload[key]
        if not isinstance(raw, str):
            errors[key] = "content must be a string"
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            errors[key] = f"invalid JSON: {exc}"
            continue
        if not isinstance(parsed, dict):
            errors[key] = "root must be a JSON object"
            continue

        err = _validate_setting(key, parsed)
        if err:
            errors[key] = err
            continue

        path = paths[key]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(parsed, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        saved.append(key)
        if key == "config":
            restart_recommended = True
        if key == "context":
            cp = str(parsed.get("config_path", "")).strip()
            if cp:
                from firefly.config.loader import set_config_path

                set_config_path(Path(cp).expanduser().resolve())

    return {
        "ok": not errors,
        "saved": saved,
        "errors": errors,
        "restart_recommended": restart_recommended,
    }
