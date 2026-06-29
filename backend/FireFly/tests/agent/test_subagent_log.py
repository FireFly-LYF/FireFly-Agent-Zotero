"""Tests for subagent debug log persistence."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from firefly.agent.runner import AgentRunResult
from firefly.agent.subagent import SubagentManager


def test_persist_subagent_run_writes_json(tmp_path: Path) -> None:
    mgr = SubagentManager(MagicMock(), tmp_path, MagicMock(), 16_000)
    result = AgentRunResult(
        final_content="done",
        messages=[
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "task"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "1", "function": {"name": "read_file"}}]},
            {"role": "tool", "tool_call_id": "1", "name": "read_file", "content": "line 1"},
        ],
        tools_used=["read_file"],
        stop_reason="completed",
        tool_events=[{"name": "read_file", "status": "ok", "detail": "paper.md"}],
    )
    started = datetime(2026, 6, 29, 12, 0, 0, tzinfo=timezone.utc)
    path = mgr._persist_subagent_run(
        task_id="abc12345",
        label="read paper",
        task="[Task]\nread",
        session_key="zotero:chat-1",
        result=result,
        ok=True,
        started_at=started,
    )
    assert path.is_file()
    assert path.parent == mgr._subagent_log_dir()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["task_id"] == "abc12345"
    assert data["ok"] is True
    assert len(data["messages"]) == 4
    assert data["tool_events"][0]["name"] == "read_file"
