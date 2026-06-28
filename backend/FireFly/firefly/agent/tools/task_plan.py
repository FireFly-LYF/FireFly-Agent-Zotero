"""任务编排工具：plan_tasks / await_stage。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from firefly.agent.tools.base import Tool, tool_parameters
from firefly.agent.tools.schema import ArraySchema, StringSchema, tool_parameters_schema

if TYPE_CHECKING:
    from firefly.agent.subagent import SubagentManager


@tool_parameters(
    tool_parameters_schema(
        stages=ArraySchema(
            StringSchema("One stage of the todo plan (executed in order)"),
            description="Ordered list of stages. Each stage may use one or more `spawn` calls.",
            min_items=1,
        ),
        required=["stages"],
    )
)
class PlanTasksTool(Tool):
    """创建会话级 todo 计划，按阶段推进。"""

    def __init__(self, manager: SubagentManager):
        self._manager = manager
        self._session_key = "zotero:direct"

    def set_context(self, channel: str, chat_id: str) -> None:
        self._session_key = f"{channel}:{chat_id}"

    @property
    def name(self) -> str:
        return "plan_tasks"

    @property
    def description(self) -> str:
        return (
            "REQUIRED first step for any multi-step task: "
            "create an ordered stage list. Then `spawn` each stage with "
            "`tools`, `skills`, and `context`; the runtime auto-waits and injects stage results."
        )

    async def execute(self, stages: list[str], **kwargs: Any) -> str:
        return self._manager.start_task_plan(self._session_key, stages)


@tool_parameters(
    tool_parameters_schema(
        description="Wait until all subagents spawned during the current stage finish.",
    )
)
class AwaitStageTool(Tool):
    """等待当前阶段全部子任务完成并进入下一阶段。"""

    def __init__(self, manager: SubagentManager):
        self._manager = manager
        self._session_key = "zotero:direct"

    def set_context(self, channel: str, chat_id: str) -> None:
        self._session_key = f"{channel}:{chat_id}"

    @property
    def name(self) -> str:
        return "await_stage"

    @property
    def description(self) -> str:
        return (
            "Block until every subagent registered for the current plan stage has "
            "completed, return their results, and advance to the next stage. "
            "Call this after all `spawn` calls for the current stage are dispatched."
        )

    async def execute(self, **kwargs: Any) -> str:
        return await self._manager.await_task_stage(self._session_key)
