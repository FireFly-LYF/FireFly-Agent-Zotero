"""Tests for subagent spawn context injection and await_stage preview limits."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from firefly.agent.orchestrator import TaskOrchestrator
from firefly.agent.runner import AgentRunSpec
from firefly.agent.subagent import SubagentManager
from firefly.agent.tools.base import Tool
from firefly.agent.tools.registry import ToolRegistry
from firefly.bus.queue import MessageBus
from firefly.config.schema import AgentDefaults, ExecToolConfig, WebToolsConfig
from firefly.session.manager import Session, SessionManager
from firefly.zotero_interface.utils import (
    ZOTERO_SPAWN_CONTEXT_KEY,
    ZOTERO_USER_REQUEST_KEY,
    extract_zotero_spawn_context,
)

_MAX = AgentDefaults().max_tool_result_chars
_SESSION = "zotero:chat-ctx"


def test_extract_zotero_spawn_context_keeps_paths_and_hints() -> None:
    raw = (
        "[zotero_current_wiki_pdf_path=/tmp/paper.pdf]\n"
        "[zotero_current_wiki_markdown_path=/tmp/paper.md]\n"
        "[zotero_literature_read_hint] grep markdown first\n"
        "总结方法\n"
    )
    ctx = extract_zotero_spawn_context(raw)
    assert "[zotero_current_wiki_markdown_path=/tmp/paper.md]" in ctx
    assert "[zotero_literature_read_hint]" in ctx
    assert "总结方法" not in ctx


def test_compose_subagent_user_content_includes_session_and_delegated() -> None:
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    workspace = MagicMock()
    sessions = SessionManager.__new__(SessionManager)
    sessions._cache = {
        _SESSION: Session(
            key=_SESSION,
            metadata={
                ZOTERO_SPAWN_CONTEXT_KEY: "[zotero_current_wiki_markdown_path=/a.md]",
                ZOTERO_USER_REQUEST_KEY: "总结方法",
            },
        )
    }
    sessions.get_or_create = lambda key: sessions._cache[key]  # type: ignore[attr-defined]

    mgr = SubagentManager(
        provider=provider,
        workspace=workspace,
        bus=bus,
        max_tool_result_chars=_MAX,
        exec_config=ExecToolConfig(enable=False),
        web_config=WebToolsConfig(enable=False),
    )
    mgr.set_session_manager(sessions)

    content, err = mgr._compose_subagent_user_content(
        task="grep 方法章节",
        context="prior notes",
        session_key=_SESSION,
    )
    assert err is None
    assert "[Zotero Runtime Context]" in content
    assert "/a.md" in content
    assert "[Delegated Context]" in content
    assert "prior notes" in content
    assert "[Original User Request]" in content
    assert "总结方法" in content
    assert "[Task]" in content
    assert "grep 方法章节" in content


def test_compose_subagent_user_content_includes_prior_stage_results() -> None:
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    mgr = SubagentManager(
        provider=provider,
        workspace=MagicMock(),
        bus=bus,
        max_tool_result_chars=_MAX,
    )
    orch = mgr._get_orchestrator(_SESSION)
    orch.completed_stage_results.append("[t1] read [ok]\nmethod A details")

    content, err = mgr._compose_subagent_user_content(
        task="write docx",
        context="docx/ out.docx",
        session_key=_SESSION,
    )
    assert err is None
    assert "[Prior Stage Results]" in content
    assert "method A details" in content


def test_spawn_rejects_without_any_context() -> None:
    async def _run() -> None:
        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"
        workspace = Path("/tmp/firefly-test-workspace")
        sessions = SessionManager.__new__(SessionManager)
        sessions._cache = {_SESSION: Session(key=_SESSION)}
        sessions.get_or_create = lambda key: sessions._cache[key]  # type: ignore[attr-defined]

        mgr = SubagentManager(
            provider=provider,
            workspace=workspace,
            bus=bus,
            max_tool_result_chars=_MAX,
        )
        mgr.set_session_manager(sessions)
        parent = ToolRegistry()
        parent.register(_StubTool("grep"))
        mgr.set_parent_registry(parent)

        msg = await mgr.spawn(
            "do work",
            session_key=_SESSION,
            tools=["grep"],
            skills=["markdown"],
        )
        assert msg.startswith("Error:")
        assert "context" in msg.lower()

    asyncio.run(_run())


def test_subagent_receives_composed_user_message() -> None:
    async def _run() -> None:
        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"
        workspace = Path("/tmp/firefly-test-workspace")
        sessions = SessionManager.__new__(SessionManager)
        session = Session(key=_SESSION)
        SubagentManager.persist_zotero_spawn_context(
            session,
            raw_user_content="[zotero_current_wiki_markdown_path=/paper.md]\n总结",
            stored_user_request="总结",
            literature_title="Paper Title",
        )
        sessions._cache = {_SESSION: session}
        sessions.get_or_create = lambda key: sessions._cache[key]  # type: ignore[attr-defined]
        sessions.save = lambda _session: None  # type: ignore[method-assign]

        captured: dict = {}

        async def fake_run(spec: AgentRunSpec):
            captured["user"] = spec.initial_messages[1]["content"]
            return MagicMock(
                stop_reason="ok",
                final_content="done",
                tool_events=[],
                error=None,
            )

        parent = ToolRegistry()
        parent.register(_StubTool("grep"))
        parent.register(_StubTool("rag_search"))
        mgr = SubagentManager(
            provider=provider,
            workspace=workspace,
            bus=bus,
            max_tool_result_chars=_MAX,
            exec_config=ExecToolConfig(enable=False),
            web_config=WebToolsConfig(enable=False),
        )
        mgr.set_session_manager(sessions)
        mgr.set_parent_registry(parent)
        mgr.runner.run = fake_run  # type: ignore[method-assign]

        await mgr.spawn(
            "read methods",
            session_key=_SESSION,
            tools=["rag_search", "grep"],
            skills=["zotero"],
            context="focus on section 3",
        )
        await asyncio.sleep(0.05)

        user = captured["user"]
        assert "/paper.md" in user
        assert "Paper Title" in user
        assert "focus on section 3" in user
        assert "read methods" in user

    asyncio.run(_run())


def test_await_stage_preview_uses_configured_limit() -> None:
    async def _run() -> None:
        orch = TaskOrchestrator(result_preview_chars=100)
        orch.start_plan(["Stage A"])
        orch.register_subtask("t1", "job", "work")
        long_result = "x" * 200
        orch.complete_subtask("t1", long_result, ok=True)
        out = await orch.await_current_stage()
        assert "x" * 97 + "..." in out
        assert "x" * 100 not in out
        assert long_result in orch.completed_stage_results[0]

    asyncio.run(_run())


class _StubTool(Tool):
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._name

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs):
        return "ok"
