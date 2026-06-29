"""Zotero 主 agent 工具集：无 plan 时直接执行，有 plan 时仅编排。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from firefly.agent.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from firefly.agent.loop import AgentLoop

ZOTERO_ORCHESTRATOR_TOOL_NAMES = ("plan_tasks", "spawn")
# Direct mode: executor tools + plan_tasks (agent opts in); spawn only after plan starts.
ZOTERO_DIRECT_MODE_EXCLUDE = frozenset({"spawn", "await_stage"})
ZOTERO_DIRECT_SKILLS = ("markdown", "zotero", "docx")


class ZoteroSessionToolRegistry:
    """按会话编排状态动态解析主 agent 可见工具（每轮 LLM 调用重新解析）。"""

    __slots__ = ("_loop", "_channel", "_session_key")

    def __init__(self, loop: AgentLoop, channel: str, session_key: str) -> None:
        self._loop = loop
        self._channel = channel
        self._session_key = session_key

    def _resolve(self) -> ToolRegistry:
        return self._loop._resolve_tools_for_llm(self._channel, self._session_key)

    def get_definitions(self) -> list[dict[str, Any]]:
        return self._resolve().get_definitions()

    def prepare_call(
        self,
        name: str,
        params: dict[str, Any],
    ) -> tuple[Any, dict[str, Any], str | None]:
        return self._resolve().prepare_call(name, params)

    async def execute(self, name: str, params: dict[str, Any]) -> Any:
        return await self._resolve().execute(name, params)

    @property
    def tool_names(self) -> list[str]:
        return self._resolve().tool_names
