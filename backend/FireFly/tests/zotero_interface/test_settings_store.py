"""Tests for Zotero plugin settings file helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from firefly.zotero_interface.settings_store import (
    read_settings_bundle,
    resolve_settings_paths,
    write_settings_bundle,
)


def test_resolve_settings_paths_nested_layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    config_path = cfg_dir / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    from firefly.config.loader import set_config_path

    set_config_path(config_path)
    paths = resolve_settings_paths()
    assert paths["config"] == config_path.resolve()
    assert paths["context"] == (tmp_path / "config" / "context.json").resolve()
    assert paths["user"] == (cfg_dir / "user.json").resolve()


def test_write_and_read_user_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    config_path = cfg_dir / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    from firefly.config.loader import set_config_path

    set_config_path(config_path)
    user_payload = {"show_llm_input": True, "markdown_rag": {"max_chars": 2000}}
    result = write_settings_bundle(
        {"user": json.dumps(user_payload, ensure_ascii=False)}
    )
    assert result["ok"] is True
    assert "user" in result["saved"]

    bundle = read_settings_bundle()
    loaded = json.loads(bundle["files"]["user"]["content"])
    assert loaded["show_llm_input"] is True
    assert loaded["markdown_rag"]["max_chars"] == 2000
