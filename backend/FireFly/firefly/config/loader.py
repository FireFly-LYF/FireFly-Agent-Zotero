"""Configuration loading utilities."""

import json
import os
import re
from pathlib import Path

import pydantic
from loguru import logger

from firefly.config.schema import Config

# Global variable to store current config path (for multi-instance support)
_current_config_path: Path | None = None


def _new_context_path(base: Path) -> Path:
    """Project-local context next to nested config (``<base>/config/context.json``)."""
    return base / "config" / "context.json"


def _legacy_context_path(base: Path) -> Path:
    return base / ".firefly" / "context.json"


def resolve_project_base_dir(config_path: Path) -> Path:
    """Directory that anchors ``config/`` for context and defaults.

    If *config_path* lives in a folder named ``config`` (e.g. ``proj/config/config.json``),
    the project base is the parent of that folder. Otherwise the base is the parent
    directory of the config file (legacy ``proj/config.json``).
    """
    p = Path(config_path).expanduser().resolve()
    if p.parent.name == "config":
        return p.parent.parent
    return p.parent


def _find_nearest_project_base(start: Path) -> Path | None:
    """Find nearest parent that contains firefly config or context (new or legacy layout)."""
    for base in [start, *start.parents]:
        if (base / "config" / "context.json").is_file():
            return base
        if (base / "config" / "config.json").is_file():
            return base
        if _legacy_context_path(base).is_file():
            return base
        if (base / "config.json").is_file():
            return base
    return None


def _load_context(base: Path) -> dict | None:
    for path in (_new_context_path(base), _legacy_context_path(base)):
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except Exception:
            continue
    return None


def resolve_default_base_dir() -> Path:
    """
    Resolve the default runtime base dir.

    Priority:
    1) FIREFLY_HOME (explicit override)
    2) nearest parent containing ``config/context.json`` (onboard) or ``config/config.json``
    3) nearest parent containing legacy ``.firefly/context.json`` or ``config.json``
    4) cwd
    """
    custom = os.environ.get("FIREFLY_HOME", "").strip()
    if custom:
        return Path(custom).expanduser().resolve()
    cwd = Path.cwd().resolve()
    found = _find_nearest_project_base(cwd)
    return found.resolve() if found else cwd


def write_project_context(*, config_path: Path, workspace: Path) -> Path:
    """Persist the last-onboarded config/workspace under ``<project>/config/context.json``."""
    project_base = resolve_project_base_dir(config_path)
    config_path = Path(config_path).expanduser().resolve()
    workspace = Path(workspace).expanduser().resolve()
    ctx_path = _new_context_path(project_base)
    ctx_path.parent.mkdir(parents=True, exist_ok=True)
    ctx_path.write_text(
        json.dumps(
            {
                "config_path": str(config_path),
                "workspace": str(workspace),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return ctx_path


def set_config_path(path: Path) -> None:
    """Set the current config path (used to derive data directory)."""
    global _current_config_path
    _current_config_path = path


def get_config_path() -> Path:
    """Get the configuration file path."""
    if _current_config_path:
        return _current_config_path
    base = resolve_default_base_dir()
    ctx = _load_context(base)
    if ctx:
        raw = str(ctx.get("config_path", "")).strip()
        if raw:
            return Path(raw).expanduser().resolve()
    nested = base / "config" / "config.json"
    legacy = base / "config.json"
    if nested.is_file():
        return nested
    if legacy.is_file():
        return legacy
    return nested


def load_config(config_path: Path | None = None) -> Config:
    """
    Load configuration from file or create default.

    Args:
        config_path: Optional path to config file. Uses default if not provided.

    Returns:
        Loaded configuration object.
    """
    path = config_path or get_config_path()

    config = Config()
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            data = _migrate_config(data)
            config = Config.model_validate(data)
        except (json.JSONDecodeError, ValueError, pydantic.ValidationError) as e:
            logger.warning(f"Failed to load config from {path}: {e}")
            logger.warning("Using default configuration.")

    _apply_ssrf_whitelist(config)
    return config


def _apply_ssrf_whitelist(config: Config) -> None:
    """Apply SSRF whitelist from config to the network security module."""
    from firefly.security.network import configure_ssrf_whitelist

    configure_ssrf_whitelist(config.tools.ssrf_whitelist)


def save_config(config: Config, config_path: Path | None = None) -> None:
    """
    Save configuration to file.

    Args:
        config: Configuration to save.
        config_path: Optional path to save to. Uses default if not provided.
    """
    path = config_path or get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    data = config.model_dump(mode="json", by_alias=True)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def resolve_config_env_vars(config: Config) -> Config:
    """Return a copy of *config* with ``${VAR}`` env-var references resolved.

    Only string values are affected; other types pass through unchanged.
    Raises :class:`ValueError` if a referenced variable is not set.
    """
    data = config.model_dump(mode="json", by_alias=True)
    data = _resolve_env_vars(data)
    return Config.model_validate(data)


def _resolve_env_vars(obj: object) -> object:
    """Recursively resolve ``${VAR}`` patterns in string values."""
    if isinstance(obj, str):
        return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", _env_replace, obj)
    if isinstance(obj, dict):
        return {k: _resolve_env_vars(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_env_vars(v) for v in obj]
    return obj


def _env_replace(match: re.Match[str]) -> str:
    name = match.group(1)
    value = os.environ.get(name)
    if value is None:
        raise ValueError(
            f"Environment variable '{name}' referenced in config is not set"
        )
    return value


def _migrate_config(data: dict) -> dict:
    """Migrate old config formats to current."""
    # Move tools.exec.restrictToWorkspace → tools.restrictToWorkspace
    tools = data.get("tools", {})
    exec_cfg = tools.get("exec", {})
    if "restrictToWorkspace" in exec_cfg and "restrictToWorkspace" not in tools:
        tools["restrictToWorkspace"] = exec_cfg.pop("restrictToWorkspace")
    return data
