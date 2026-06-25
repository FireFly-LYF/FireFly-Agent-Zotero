"""Agent temp/ scratch file rules and cleanup."""

from __future__ import annotations

from pathlib import Path

from firefly.agent.temp_workspace import (
    begin_agent_temp_turn,
    cleanup_agent_temp_turn,
    get_agent_temp_dir,
    register_agent_temp_file,
    validate_scratch_write_path,
)


def test_scratch_script_must_use_temp(tmp_path: Path) -> None:
    ws = tmp_path / "workspace"
    ws.mkdir()
    bad = ws / "run.py"
    err = validate_scratch_write_path(bad, ws)
    assert err is not None
    assert "temp" in err

    ok = get_agent_temp_dir(ws) / "run.py"
    assert validate_scratch_write_path(ok, ws) is None


def test_skills_path_allowed(tmp_path: Path) -> None:
    ws = tmp_path / "workspace"
    skill_py = ws / "skills" / "demo" / "tool.py"
    skill_py.parent.mkdir(parents=True)
    assert validate_scratch_write_path(skill_py, ws) is None


def test_cleanup_removes_tracked_temp_files(tmp_path: Path) -> None:
    ws = tmp_path / "workspace"
    temp_dir = get_agent_temp_dir(ws)
    scratch = temp_dir / "scratch.py"
    scratch.write_text("x", encoding="utf-8")
    bridge = temp_dir / "zotero-abc.png"
    bridge.write_bytes(b"png")

    begin_agent_temp_turn()
    register_agent_temp_file(scratch, ws)
    register_agent_temp_file(bridge, ws)
    cleanup_agent_temp_turn(ws)

    assert not scratch.exists()
    assert bridge.is_file()
