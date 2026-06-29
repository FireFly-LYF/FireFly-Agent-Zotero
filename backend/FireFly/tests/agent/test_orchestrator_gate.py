"""Tests for orchestrator gate blocking premature final replies."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from firefly.agent.runner import AgentRunSpec, AgentRunner
from firefly.agent.subagent import SubagentManager
from firefly.agent.tools.base import Tool
from firefly.agent.tools.registry import ToolRegistry
from firefly.bus.queue import MessageBus


class _NoopTool(Tool):
    @property
    def name(self) -> str:
        return "noop"

    @property
    def description(self) -> str:
        return "noop"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs):
        return "ok"


def test_runner_orchestrator_gate_blocks_then_continues() -> None:
    async def _run() -> None:
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"
        provider.generation.max_tokens = 4096

        calls = {"n": 0}
        gate_calls = {"n": 0}

        async def fake_chat(**kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return MagicMock(
                    content="阶段1进行中，预期输出 docx",
                    tool_calls=[],
                    reasoning_content=None,
                    thinking_blocks=None,
                    usage={},
                    finish_reason="stop",
                    has_tool_calls=False,
                )
            return MagicMock(
                content="最终总结：方法已写入 docx/抗干扰方法总结.docx",
                tool_calls=[],
                reasoning_content=None,
                thinking_blocks=None,
                usage={},
                finish_reason="stop",
                has_tool_calls=False,
            )

        provider.chat_with_retry = fake_chat

        async def gate():
            gate_calls["n"] += 1
            if gate_calls["n"] == 1:
                return "[Orchestration gate] spawn stage 2 before replying."
            return None

        tools = ToolRegistry()
        tools.register(_NoopTool())

        runner = AgentRunner(provider)
        result = await runner.run(AgentRunSpec(
            initial_messages=[
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "总结抗干扰方法并写 docx"},
            ],
            tools=tools,
            model="test-model",
            max_iterations=5,
            max_tool_result_chars=8000,
            session_key="zotero:chat-1",
            orchestrator_gate=gate,
        ))

        assert gate_calls["n"] >= 1
        assert result.final_content == "最终总结：方法已写入 docx/抗干扰方法总结.docx"
        injected = [
            m for m in result.messages
            if m.get("role") == "user" and "Orchestration gate" in str(m.get("content"))
        ]
        assert len(injected) == 1

    asyncio.run(_run())


def test_subagent_orchestrator_gate_prompts_spawn_when_stage_idle(tmp_path) -> None:
    async def _run() -> None:
        bus = MessageBus()
        provider = MagicMock()
        mgr = SubagentManager(
            provider=provider,
            workspace=tmp_path,
            bus=bus,
            max_tool_result_chars=8000,
        )
        mgr.start_task_plan("zotero:chat-1", ["Extract methods", "Write docx"])
        msg = await mgr.orchestrator_gate("zotero:chat-1")
        assert msg is not None
        assert "Orchestration gate" in msg
        assert "spawn" in msg.lower()

    asyncio.run(_run())
