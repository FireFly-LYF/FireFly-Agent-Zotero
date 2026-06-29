"""Runtime path helpers derived from the active config context."""

from __future__ import annotations

from pathlib import Path

from firefly.config.loader import get_config_path, resolve_default_base_dir
from firefly.utils.helpers import ensure_dir


def get_data_dir() -> Path:
    """Return the instance-level runtime data directory."""
    return ensure_dir(get_config_path().parent)


def get_runtime_subdir(name: str) -> Path:
    """Return a named runtime subdirectory under the instance data dir."""
    return ensure_dir(get_data_dir() / name)


def get_media_dir(channel: str | None = None) -> Path:
    """Return the media directory, optionally namespaced per channel."""
    base = get_runtime_subdir("media")
    return ensure_dir(base / channel) if channel else base


def get_cron_dir() -> Path:
    """Return the cron storage directory."""
    return get_runtime_subdir("cron")


def get_logs_dir() -> Path:
    """Return the logs directory."""
    return get_runtime_subdir("logs")


def get_workspace_path(workspace: str | None = None) -> Path:
    """Resolve and ensure the agent workspace path."""
    path = Path(workspace).expanduser() if workspace else resolve_default_base_dir() / "workspace"
    return ensure_dir(path)


def get_workspace_temp_dir(workspace: str | Path | None = None) -> Path:
    """Return ``workspace/temp`` for agent scratch files and bridge media cache."""
    return ensure_dir(get_workspace_path(str(workspace) if workspace is not None else None) / "temp")


def get_llm_wiki_root() -> Path:
    """Return ``llm-wiki`` root (``backend/llm-wiki`` in this repo layout)."""
    from firefly.config.loader import get_config_path, resolve_project_base_dir
    from firefly.skills.wiki.scripts.llm_wiki_paths import resolve_llm_wiki_root

    found = resolve_llm_wiki_root(get_workspace_path())
    if found is not None:
        return found
    base = resolve_project_base_dir(get_config_path())
    for cand in (base / "llm-wiki", base / "backend" / "llm-wiki"):
        if cand.is_dir():
            return cand.resolve()
    return ensure_dir(base / "llm-wiki")


def get_docx_dir() -> Path:
    """Return ``llm-wiki/docx`` — default location for user .docx deliverables."""
    return ensure_dir(get_llm_wiki_root() / "docx")


def is_default_workspace(workspace: str | Path | None) -> bool:
    """Return whether a workspace resolves to firefly's default workspace path."""
    current = Path(workspace).expanduser() if workspace is not None else resolve_default_base_dir() / "workspace"
    default = resolve_default_base_dir() / "workspace"
    return current.resolve(strict=False) == default.resolve(strict=False)


def get_cli_history_path() -> Path:
    """Return the CLI history file path (under the same directory as ``config.json``)."""
    return get_config_path().parent / "history" / "cli_history"


def get_cli_prefs_path() -> Path:
    """Return local prefs sidecar path (``user.json``; same directory as ``config.json``)."""
    from firefly.config.cli_prefs import cli_prefs_path_for

    return cli_prefs_path_for(get_config_path())


def get_bridge_install_dir() -> Path:
    """Return the shared WhatsApp bridge installation directory."""
    return resolve_default_base_dir() / "bridge"


def get_legacy_sessions_dir() -> Path:
    """Return the legacy global session directory used for migration fallback."""
    return resolve_default_base_dir() / "sessions"
