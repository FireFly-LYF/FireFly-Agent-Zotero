"""Integration tests: plan_tasks + spawn + await_stage."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from firefly.agent.runner import AgentRunSpec
from firefly.agent.subagent import SubagentManager
from firefly.agent.tools.base import Tool
from firefly.agent.tools.registry import ToolRegistry
from firefly.bus.queue import MessageBus

_MAX = 8000
_SESSION = "zotero:chat-1"


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


def test_spawn_under_plan_defers_bus_announce(tmp_path) -> None:
    async def _run() -> None:
        bus = MessageBus()
        published: list = []
        bus.publish_inbound = AsyncMock(side_effect=lambda m: published.append(m))

        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"

        async def fake_run(spec: AgentRunSpec):
            return MagicMock(
                stop_reason="ok",
                final_content="verified ok",
                tool_events=[],
                error=None,
            )

        mgr = SubagentManager(
            provider=provider,
            workspace=tmp_path,
            bus=bus,
            max_tool_result_chars=_MAX,
        )
        parent = ToolRegistry()
        parent.register(_StubTool("mcp_docx-mcp_search_text"))
        mgr.set_parent_registry(parent)
        mgr.runner.run = fake_run  # type: ignore[method-assign]
        mgr.start_task_plan(_SESSION, ["Verify docx"])

        msg = await mgr.spawn(
            "verify",
            label="v",
            session_key=_SESSION,
            tools=["mcp_docx-mcp_search_text"],
            skills=["docx"],
            context="target_docx: docx/out.docx",
        )
        assert "auto-wait" in msg.lower()
        await asyncio.sleep(0.05)
        assert published == []

        out = await mgr.await_task_stage(_SESSION)
        assert "verified ok" in out
        assert "Stage 1 complete" in out

    asyncio.run(_run())


def test_await_stage_waits_for_multiple_spawns(tmp_path) -> None:
    async def _run() -> None:
        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"
        call_count = {"n": 0}

        async def fake_run(spec: AgentRunSpec):
            call_count["n"] += 1
            await asyncio.sleep(0.03)
            return MagicMock(
                stop_reason="ok",
                final_content=f"done-{call_count['n']}",
                tool_events=[],
                error=None,
            )

        mgr = SubagentManager(
            provider=provider,
            workspace=tmp_path,
            bus=bus,
            max_tool_result_chars=_MAX,
        )
        parent = ToolRegistry()
        parent.register(_StubTool("grep"))
        mgr.set_parent_registry(parent)
        mgr.runner.run = fake_run  # type: ignore[method-assign]

        mgr.start_task_plan(_SESSION, ["Parallel work"])
        await mgr.spawn(
            "a", session_key=_SESSION, tools=["grep"], skills=["markdown"], context="job a",
        )
        await mgr.spawn(
            "b", session_key=_SESSION, tools=["grep"], skills=["markdown"], context="job b",
        )

        out = await mgr.await_task_stage(_SESSION)
        assert call_count["n"] == 2
        assert "done-1" in out or "done-2" in out
        assert "All stages complete" in out

    asyncio.run(_run())
