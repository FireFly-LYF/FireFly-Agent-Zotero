from pathlib import Path

from firefly.config.paths import (
    get_bridge_install_dir,
    get_cli_history_path,
    get_cron_dir,
    get_data_dir,
    get_docx_dir,
    get_legacy_sessions_dir,
    get_logs_dir,
    get_media_dir,
    get_runtime_subdir,
    get_workspace_path,
    is_default_workspace,
)


def test_runtime_dirs_follow_config_path(monkeypatch, tmp_path: Path) -> None:
    config_file = tmp_path / "instance-a" / "config.json"
    monkeypatch.setattr("firefly.config.paths.get_config_path", lambda: config_file)

    assert get_data_dir() == config_file.parent
    assert get_runtime_subdir("cron") == config_file.parent / "cron"
    assert get_cron_dir() == config_file.parent / "cron"
    assert get_logs_dir() == config_file.parent / "logs"


def test_media_dir_supports_channel_namespace(monkeypatch, tmp_path: Path) -> None:
    config_file = tmp_path / "instance-b" / "config.json"
    monkeypatch.setattr("firefly.config.paths.get_config_path", lambda: config_file)

    assert get_media_dir() == config_file.parent / "media"
    assert get_media_dir("telegram") == config_file.parent / "media" / "telegram"


def test_cli_history_follows_config_parent(monkeypatch, tmp_path: Path) -> None:
    config_file = tmp_path / "proj" / "config" / "config.json"
    monkeypatch.setattr("firefly.config.paths.get_config_path", lambda: config_file)

    assert get_cli_history_path() == tmp_path / "proj" / "config" / "history" / "cli_history"


def test_bridge_and_legacy_sessions_use_project_base(monkeypatch, tmp_path: Path) -> None:
    base = tmp_path / "proj"
    monkeypatch.setattr("firefly.config.paths.resolve_default_base_dir", lambda: base)

    assert get_bridge_install_dir() == base / "bridge"
    assert get_legacy_sessions_dir() == base / "sessions"


def test_workspace_path_is_explicitly_resolved(monkeypatch, tmp_path: Path) -> None:
    iso = tmp_path / "iso"
    monkeypatch.setattr("firefly.config.paths.resolve_default_base_dir", lambda: iso)

    assert get_workspace_path() == iso / "workspace"
    assert get_workspace_path("~/custom-workspace") == Path.home() / "custom-workspace"


def test_docx_dir_under_llm_wiki(monkeypatch, tmp_path: Path) -> None:
    backend = tmp_path / "backend"
    wiki = backend / "llm-wiki" / "wiki"
    wiki.mkdir(parents=True)
    config_file = backend / "config" / "config.json"
    ws = backend / "workspace"
    ws.mkdir()
    monkeypatch.setattr("firefly.config.paths.get_config_path", lambda: config_file)
    monkeypatch.setattr("firefly.config.paths.resolve_default_base_dir", lambda: backend)
    monkeypatch.setattr("firefly.config.paths.get_workspace_path", lambda _w=None: ws)

    assert get_docx_dir() == (backend / "llm-wiki" / "docx").resolve()
    assert get_docx_dir().is_dir()


def test_is_default_workspace_distinguishes_default_and_custom_paths(
    monkeypatch, tmp_path: Path
) -> None:
    iso = tmp_path / "iso"
    monkeypatch.setattr("firefly.config.paths.resolve_default_base_dir", lambda: iso)

    assert is_default_workspace(None) is True
    assert is_default_workspace(iso / "workspace") is True
    assert is_default_workspace("~/custom-workspace") is False
