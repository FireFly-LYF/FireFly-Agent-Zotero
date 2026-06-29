"""Tests for subagent task profiles (tools/skills specified by main agent)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from firefly.agent.runner import AgentRunSpec
from firefly.agent.subagent import SubagentManager
from firefly.agent.tools.base import Tool
from firefly.agent.tools.registry import ToolRegistry
from firefly.bus.queue import MessageBus
from firefly.config.schema import ExecToolConfig, WebToolsConfig

_MAX_TOOL_RESULT_CHARS = 8000


class _StubTool(Tool):
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"stub {self._name}"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs):
        return "ok"


def test_spawn_without_plan_tasks_rejected(tmp_path) -> None:
    async def _run() -> None:
        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"
        mgr = SubagentManager(
            provider=provider,
            workspace=tmp_path,
            bus=bus,
            max_tool_result_chars=8000,
        )
        parent = ToolRegistry()
        parent.register(_StubTool("rag_search"))
        mgr.set_parent_registry(parent)

        msg = await mgr.spawn(
            "find SLR definition",
            session_key="zotero:chat-1",
            tools=["rag_search"],
            skills=["markdown", "zotero"],
            context="markdown path",
        )
        assert msg.startswith("Error:")
        assert "plan_tasks" in msg

    asyncio.run(_run())


def test_spawn_rejects_docx_create_without_verify_tools(tmp_path) -> None:
    async def _run() -> None:
        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"
        mgr = SubagentManager(
            provider=provider,
            workspace=tmp_path,
            bus=bus,
            max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        )
        parent = ToolRegistry()
        parent.register(_StubTool("mcp_docx-mcp_create_from_markdown"))
        parent.register(_StubTool("mcp_docx-mcp_save_document"))
        mgr.set_parent_registry(parent)

        msg = await mgr.spawn(
            "write docx",
            tools=["mcp_docx-mcp_create_from_markdown", "mcp_docx-mcp_save_document"],
            skills=["docx"],
            context="markdown body in prior stage",
        )
        assert msg.startswith("Error:")
        assert "open_document" in msg
        assert "get_body_text" in msg

    asyncio.run(_run())


def test_spawn_labels_increment_per_session(tmp_path) -> None:
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    mgr = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=bus,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
    )
    assert mgr._alloc_spawn_label("zotero:chat-1") == "spawn 1"
    assert mgr._alloc_spawn_label("zotero:chat-1") == "spawn 2"
    mgr.reset_spawn_labels("zotero:chat-1")
    assert mgr._alloc_spawn_label("zotero:chat-1") == "spawn 1"


def test_spawn_rejects_procedural_task_with_tool_names(tmp_path) -> None:
    async def _run() -> None:
        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"
        mgr = SubagentManager(
            provider=provider,
            workspace=tmp_path,
            bus=bus,
            max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        )
        parent = ToolRegistry()
        for name in ("rag_search", "get_markdown_headings", "read_file"):
            parent.register(_StubTool(name))
        mgr.set_parent_registry(parent)

        msg = await mgr.spawn(
            "读取论文标题结构，使用get_markdown_headings获取论文的完整标题结构",
            tools=["rag_search", "get_markdown_headings", "read_file"],
            skills=["markdown", "zotero"],
            context="markdown path in context",
        )
        assert msg.startswith("Error:")
        assert "deliverable" in msg.lower() or "get_markdown_headings" in msg

    asyncio.run(_run())


def test_spawn_accepts_deliverable_task(tmp_path) -> None:
    async def _run() -> None:
        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"
        mgr = SubagentManager(
            provider=provider,
            workspace=tmp_path,
            bus=bus,
            max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        )
        parent = ToolRegistry()
        for name in ("rag_search", "get_markdown_headings", "read_file"):
            parent.register(_StubTool(name))
        mgr.set_parent_registry(parent)
        mgr.runner.run = AsyncMock(return_value=MagicMock(
            stop_reason="completed",
            final_content="notes",
            messages=[],
            tool_events=[],
            tools_used=[],
        ))

        msg = await mgr.spawn(
            "从论文 markdown 提取抗干扰方法步骤与关键公式，返回结构化笔记",
            tools=["rag_search", "get_markdown_headings", "read_file"],
            skills=["markdown", "zotero"],
            context="path=/tmp/paper.md",
            session_key=None,
        )
        assert "started" in msg.lower()

    asyncio.run(_run())


def test_spawn_rejects_missing_tools(tmp_path) -> None:
    async def _run() -> None:
        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"
        mgr = SubagentManager(
            provider=provider,
            workspace=tmp_path,
            bus=bus,
            max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        )

        msg = await mgr.spawn("task", skills=["markdown"])
        assert msg.startswith("Error:")
        assert "tools" in msg.lower()

    asyncio.run(_run())


def test_spawn_rejects_zotero_skill_without_rag_search(tmp_path) -> None:
    async def _run() -> None:
        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"
        mgr = SubagentManager(
            provider=provider,
            workspace=tmp_path,
            bus=bus,
            max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        )
        parent = ToolRegistry()
        parent.register(_StubTool("read_file"))
        mgr.set_parent_registry(parent)

        msg = await mgr.spawn(
            "extract methods",
            tools=["read_file"],
            skills=["zotero"],
            context="markdown_path: /paper.md",
        )
        assert msg.startswith("Error:")
        assert "rag_search" in msg

    asyncio.run(_run())


def test_spawn_rejects_unknown_tools(tmp_path) -> None:
    async def _run() -> None:
        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"
        mgr = SubagentManager(
            provider=provider,
            workspace=tmp_path,
            bus=bus,
            max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        )
        parent = ToolRegistry()
        parent.register(_StubTool("grep"))
        mgr.set_parent_registry(parent)

        msg = await mgr.spawn("task", tools=["grep", "missing_tool"], skills=["markdown"])
        assert msg.startswith("Error:")
        assert "missing_tool" in msg

    asyncio.run(_run())


def test_spawn_rejects_blocked_tools(tmp_path) -> None:
    async def _run() -> None:
        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"
        mgr = SubagentManager(
            provider=provider,
            workspace=tmp_path,
            bus=bus,
            max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        )
        parent = ToolRegistry()
        parent.register(_StubTool("spawn"))
        mgr.set_parent_registry(parent)

        msg = await mgr.spawn("task", tools=["spawn"], skills=["markdown"])
        assert "cannot use" in msg

    asyncio.run(_run())


def test_subagent_uses_profile_tools_only(tmp_path) -> None:
    async def _run() -> None:
        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"

        captured: dict = {}

        async def fake_run(spec: AgentRunSpec):
            captured["tool_names"] = spec.tools.tool_names
            captured["system"] = spec.initial_messages[0]["content"]
            return MagicMock(
                stop_reason="ok",
                final_content="done",
                tool_events=[],
                error=None,
            )

        parent = ToolRegistry()
        parent.register(_StubTool("mcp_docx-mcp_get_headings"))
        parent.register(_StubTool("mcp_docx-mcp_search_text"))
        parent.register(_StubTool("grep"))

        mgr = SubagentManager(
            provider=provider,
            workspace=tmp_path,
            bus=bus,
            max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        )
        mgr.set_parent_registry(parent)
        mgr.runner.run = fake_run  # type: ignore[method-assign]
        mgr._announce_result = AsyncMock()

        await mgr._run_subagent(
            "t1",
            "verify docx",
            "verify",
            {"channel": "zotero", "chat_id": "c1"},
            tool_names=["mcp_docx-mcp_get_headings", "mcp_docx-mcp_search_text"],
            skill_names=["docx"],
        )

        assert set(captured["tool_names"]) == {
            "mcp_docx-mcp_get_headings",
            "mcp_docx-mcp_search_text",
        }
        assert "Allowed tools" in captured["system"]
        assert "mcp_docx-mcp_search_text" in captured["system"]
        assert "Active Skills" in captured["system"]
        assert "Execution style" in captured["system"]
        assert "Verify deliverable" in captured["system"]

    asyncio.run(_run())


def test_subagent_default_tools_without_profile(tmp_path) -> None:
    async def _run() -> None:
        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"

        captured: dict = {}

        async def fake_run(spec: AgentRunSpec):
            captured["tool_names"] = set(spec.tools.tool_names)
            return MagicMock(
                stop_reason="ok",
                final_content="done",
                tool_events=[],
                error=None,
            )

        mgr = SubagentManager(
            provider=provider,
            workspace=tmp_path,
            bus=bus,
            max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
            exec_config=ExecToolConfig(enable=False),
            web_config=WebToolsConfig(enable=False),
        )
        mgr.runner.run = fake_run  # type: ignore[method-assign]
        mgr._announce_result = AsyncMock()

        await mgr._run_subagent(
            "t1", "list files", "list", {"channel": "cli", "chat_id": "c1"},
        )

        assert "grep" in captured["tool_names"]
        assert "read_file" in captured["tool_names"]
        assert not any(n.startswith("mcp_") for n in captured["tool_names"])

    asyncio.run(_run())


def test_registry_subset() -> None:
    reg = ToolRegistry()
    reg.register(_StubTool("a"))
    reg.register(_StubTool("b"))
    sub, missing = reg.subset(["a", "c"])
    assert sub.tool_names == ["a"]
    assert missing == ["c"]
