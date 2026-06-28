"""Tests: main agent exposes orchestration tools only."""

from __future__ import annotations

from pathlib import Path

from unittest.mock import MagicMock

from firefly.agent.context import ContextBuilder
from firefly.agent.loop import ZOTERO_ORCHESTRATOR_TOOL_NAMES, AgentLoop
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


def test_tools_for_llm_orchestrator_subset() -> None:
    loop = _make_loop(Path("/tmp/firefly-zotero-test"))
    all_names = set(loop.tools.tool_names)
    assert len(all_names) > len(ZOTERO_ORCHESTRATOR_TOOL_NAMES)

    exposed = loop._tools_for_llm("zotero")
    assert set(exposed.tool_names) == set(ZOTERO_ORCHESTRATOR_TOOL_NAMES)
    assert len(exposed.get_definitions()) == len(ZOTERO_ORCHESTRATOR_TOOL_NAMES)
    assert "await_stage" not in exposed.tool_names


def test_system_prompt_includes_delegation_catalog() -> None:
    loop = _make_loop(Path("/tmp/firefly-zotero-prompt"))
    prompt = loop.context.build_system_prompt()

    assert "Delegation catalog" in prompt
    assert "<skills>" in prompt
    assert "<executor_tools>" in prompt
    assert "rag_search" in prompt
    catalog_tools = prompt.split("<executor_tools>", 1)[1].split("</executor_tools>", 1)[0]
    assert "plan_tasks" not in catalog_tools
    assert "spawn" not in catalog_tools


def test_system_prompt_uses_zotero_orchestration() -> None:
    builder = ContextBuilder(Path("/tmp/firefly-zotero-prompt"))

    prompt = builder.build_system_prompt()

    assert "Active Skills" not in prompt
    assert "plan_tasks" in prompt
    assert "spawn" in prompt
    assert "auto-waits" in prompt.lower() or "auto-wait" in prompt.lower()
