"""Tests for ``user.json`` next to ``config.json``."""

import json
from pathlib import Path

from firefly.config.cli_prefs import (
    DEFAULT_CLI_FILE_BOOTSTRAP,
    DEFAULT_CLI_PREFS,
    cli_prefs_path_for,
    ensure_cli_prefs_file,
    load_cli_prefs,
)


def test_cli_prefs_path_same_dir_as_config(tmp_path: Path) -> None:
    cfg = tmp_path / "nested" / "config" / "config.json"
    assert cli_prefs_path_for(cfg) == tmp_path / "nested" / "config" / "user.json"


def test_ensure_cli_prefs_creates_once(tmp_path: Path) -> None:
    cfg = tmp_path / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("{}", encoding="utf-8")
    p1 = ensure_cli_prefs_file(cfg)
    p2 = ensure_cli_prefs_file(cfg)
    assert p1 == p2
    assert p1.name == "user.json"
    data = json.loads(p1.read_text(encoding="utf-8"))
    assert data == DEFAULT_CLI_FILE_BOOTSTRAP


def test_load_cli_prefs_merges_llm_input_print(tmp_path: Path, monkeypatch) -> None:
    cfg = tmp_path / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    user = cfg.parent / "user.json"
    user.write_text(
        json.dumps(
            {"show_llm_input": True, "llm_input_print": {"system": True, "rag": "1"}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("firefly.config.loader.get_config_path", lambda: cfg)
    p = load_cli_prefs()
    assert p["llm_input_print"]["system"] is True
    assert p["llm_input_print"]["rag"] is True
    assert p["llm_input_print"]["tools"] is False


def test_load_cli_prefs_reads_file(tmp_path: Path, monkeypatch) -> None:
    cfg = tmp_path / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    user = cfg.parent / "user.json"
    user.write_text(json.dumps({"show_llm_input": True}, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr("firefly.config.loader.get_config_path", lambda: cfg)
    assert load_cli_prefs()["show_llm_input"] is True
    assert load_cli_prefs(cfg)["show_llm_input"] is True


def test_load_cli_prefs_missing_uses_defaults(tmp_path: Path, monkeypatch) -> None:
    cfg = tmp_path / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("firefly.config.loader.get_config_path", lambda: cfg)
    got = load_cli_prefs()
    assert got["show_llm_input"] == DEFAULT_CLI_PREFS["show_llm_input"]
    assert got["markdown_rag"] == DEFAULT_CLI_PREFS["markdown_rag"]


def test_load_cli_prefs_markdown_rag(tmp_path: Path, monkeypatch) -> None:
    cfg = tmp_path / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    (cfg.parent / "user.json").write_text(
        json.dumps({"markdown_rag": {"max_chars": 2400}}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr("firefly.config.loader.get_config_path", lambda: cfg)
    assert load_cli_prefs(cfg)["markdown_rag"]["max_chars"] == 2400
