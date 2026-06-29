"""Tests: Zotero main agent direct vs orchestration tool exposure."""

from __future__ import annotations

from pathlib import Path

from unittest.mock import MagicMock

from firefly.agent.context import ContextBuilder
from firefly.agent.loop import AgentLoop
from firefly.agent.toolregistry import (
    ZOTERO_ORCHESTRATOR_TOOL_NAMES,
)
from firefly.bus.queue import MessageBus


def _make_loop(workspace) -> AgentLoop:
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation.max_tokens = 4096
    return AgentLoop(
        bus=bus,
        provider=provider,
        workspace=workspace,
        model="test-model",
    )


def test_tools_direct_mode_exposes_rag_and_plan_tasks_not_spawn() -> None:
    loop = _make_loop(Path("/tmp/firefly-zotero-direct"))
    exposed = loop._resolve_tools_for_llm("zotero", "zotero:chat-1")
    names = set(exposed.tool_names)
    assert "rag_search" in names
    assert "read_file" in names
    assert "plan_tasks" in names
    assert "spawn" not in names


def test_tools_orchestration_mode_subset() -> None:
    loop = _make_loop(Path("/tmp/firefly-zotero-orch"))
    loop.subagents.start_task_plan("zotero:chat-1", ["Extract", "Write docx"])
    exposed = loop._resolve_tools_for_llm("zotero", "zotero:chat-1")
    assert set(exposed.tool_names) == set(ZOTERO_ORCHESTRATOR_TOOL_NAMES)


def test_system_prompt_direct_includes_active_skills() -> None:
    builder = ContextBuilder(Path("/tmp/firefly-zotero-prompt"))
    prompt = builder.build_system_prompt(orchestration_mode=False)
    assert "Active Skills" in prompt
    assert "you choose the mode" in prompt.lower()
    assert "plan_tasks" in prompt


def test_system_prompt_orchestration_includes_delegation_catalog() -> None:
    loop = _make_loop(Path("/tmp/firefly-zotero-prompt-orch"))
    prompt = loop.context.build_system_prompt(orchestration_mode=True)
    assert "Delegation catalog" in prompt
    assert "Zotero orchestration (active" in prompt
    assert "Active Skills" not in prompt


def test_reset_orchestrator_clears_plan() -> None:
    loop = _make_loop(Path("/tmp/firefly-zotero-reset"))
    key = "zotero:chat-1"
    loop.subagents.start_task_plan(key, ["Stage A"])
    assert loop._is_orchestration_active(key)
    loop.subagents.reset_orchestrator(key)
    assert not loop._is_orchestration_active(key)
    exposed = loop._resolve_tools_for_llm("zotero", key)
    assert "spawn" not in exposed.tool_names
    assert "plan_tasks" in exposed.tool_names
