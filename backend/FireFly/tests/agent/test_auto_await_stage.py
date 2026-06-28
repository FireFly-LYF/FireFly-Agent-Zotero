"""Tests for auto-await after spawn batch."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from firefly.agent.runner import AgentRunSpec, AgentRunner
from firefly.agent.tools.base import Tool
from firefly.agent.tools.registry import ToolRegistry
from firefly.providers.base import ToolCallRequest


class _SpawnTool(Tool):
    @property
    def name(self) -> str:
        return "spawn"

    @property
    def description(self) -> str:
        return "spawn"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs):
        return "started"


def test_runner_auto_await_injects_stage_results() -> None:
    async def _run() -> None:
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"
        provider.generation.max_tokens = 4096

        calls = {"n": 0}

        async def fake_chat(**kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return MagicMock(
                    content="",
                    tool_calls=[ToolCallRequest(id="c1", name="spawn", arguments={})],
                    reasoning_content=None,
                    thinking_blocks=None,
                    usage={},
                    finish_reason="tool_calls",
                    has_tool_calls=True,
                )
            return MagicMock(
                content="done",
                tool_calls=[],
                reasoning_content=None,
                thinking_blocks=None,
                usage={},
                finish_reason="stop",
                has_tool_calls=False,
            )

        provider.chat_with_retry = fake_chat

        auto_await = AsyncMock(return_value="Stage 1 complete: ok")

        tools = ToolRegistry()
        tools.register(_SpawnTool())

        runner = AgentRunner(provider)
        result = await runner.run(AgentRunSpec(
            initial_messages=[
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "go"},
            ],
            tools=tools,
            model="test-model",
            max_iterations=5,
            max_tool_result_chars=8000,
            session_key="zotero:chat-1",
            auto_await_stage=auto_await,
        ))

        auto_await.assert_awaited_once()
        assert result.final_content == "done"
        injected = [
            m for m in result.messages
            if m.get("role") == "user" and "[Stage Results]" in str(m.get("content"))
        ]
        assert len(injected) == 1
        assert "Stage 1 complete" in str(injected[0]["content"])

    asyncio.run(_run())
