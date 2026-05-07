"""Tests for nested ``config/`` layout vs legacy root ``config.json``."""

import json
from pathlib import Path

import pytest

from firefly.config.loader import (
    get_config_path,
    resolve_project_base_dir,
    write_project_context,
)


@pytest.fixture(autouse=True)
def clear_config_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("firefly.config.loader._current_config_path", None)


def test_get_config_path_prefers_nested_config_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    proj = tmp_path / "myproj"
    nested = proj / "config" / "config.json"
    nested.parent.mkdir(parents=True)
    nested.write_text("{}", encoding="utf-8")
    monkeypatch.chdir(proj)

    assert get_config_path() == nested


def test_get_config_path_legacy_root_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    proj = tmp_path / "legacy"
    proj.mkdir()
    legacy = proj / "config.json"
    legacy.write_text("{}", encoding="utf-8")
    monkeypatch.chdir(proj)

    assert get_config_path() == legacy


def test_get_config_path_context_overrides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    proj = tmp_path / "ctx"
    proj.mkdir()
    monkeypatch.chdir(proj)
    wanted = proj / "config" / "config.json"
    wanted.parent.mkdir(parents=True)
    wanted.write_text("{}", encoding="utf-8")
    (proj / "config" / "context.json").write_text(
        json.dumps({"config_path": str(proj / "other.json"), "workspace": str(proj / "ws")}),
        encoding="utf-8",
    )
    (proj / "other.json").write_text("{}", encoding="utf-8")

    assert get_config_path() == proj / "other.json"


def test_resolve_project_base_dir_nested_vs_legacy(tmp_path: Path) -> None:
    nested = tmp_path / "p" / "config" / "config.json"
    legacy = tmp_path / "p" / "config.json"
    assert resolve_project_base_dir(nested) == tmp_path / "p"
    assert resolve_project_base_dir(legacy) == tmp_path / "p"


def test_write_project_context_writes_config_context_json(tmp_path: Path) -> None:
    proj = tmp_path / "p"
    cfg = proj / "config" / "config.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("{}", encoding="utf-8")
    ws = proj / "workspace"
    ws.mkdir()

    out = write_project_context(config_path=cfg, workspace=ws)

    assert out == proj / "config" / "context.json"
    data = json.loads(out.read_text(encoding="utf-8"))
    assert Path(data["config_path"]) == cfg
    assert Path(data["workspace"]) == ws
