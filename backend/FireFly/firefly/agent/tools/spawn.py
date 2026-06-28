"""用于创建后台子代理的 spawn 工具。"""

from typing import TYPE_CHECKING, Any

from firefly.agent.tools.base import Tool, tool_parameters
from firefly.agent.tools.schema import ArraySchema, StringSchema, tool_parameters_schema

if TYPE_CHECKING:
    from firefly.agent.subagent import SubagentManager


@tool_parameters(
    tool_parameters_schema(
        task=StringSchema(
            "What the subagent must do. Keep this focused; put paths, prior-stage "
            "outputs, and reference material in `context`."
        ),
        label=StringSchema("Optional short label for the task (for display)"),
        context=StringSchema(
            "All execution context the subagent needs: markdown/pdf paths, section "
            "anchors, formulas or excerpts from prior stages, output paths, constraints. "
            "Required on Zotero when session markers are unavailable; always include "
            "prior-stage deliverables for stage 2+."
        ),
        tools=ArraySchema(
            StringSchema("Registered tool name (exact match, e.g. mcp_docx-mcp_search_text)"),
            description=(
                "Tool names the subagent may call — exact names from Delegation catalog. "
                "Literature stage: [\"rag_search\", \"read_file\", \"grep\"]. Required on every spawn."
            ),
            min_items=1,
        ),
        skills=ArraySchema(
            StringSchema("Skill directory name (e.g. docx, zotero, markdown)"),
            description=(
                "Skills inlined into the subagent prompt. "
                "Literature stage: [\"markdown\", \"zotero\"]. Required on every spawn."
            ),
            min_items=1,
        ),
        required=["task", "tools", "skills"],
    )
)
class SpawnTool(Tool):
    """用于启动子代理执行后台任务的工具。"""

    def __init__(self, manager: "SubagentManager"):
        self._manager = manager
        self._origin_channel = "zotero"
        self._origin_chat_id = "direct"
        self._session_key = "zotero:direct"

    def set_context(self, channel: str, chat_id: str) -> None:
        """设置子代理回报结果时的来源上下文。"""
        self._origin_channel = channel
        self._origin_chat_id = chat_id
        self._session_key = f"{channel}:{chat_id}"

    @property
    def name(self) -> str:
        return "spawn"

    @property
    def description(self) -> str:
        return (
            "Delegate execution to a subagent (REQUIRED after plan_tasks). "
            "Always pass `tools`, `skills`, and `context`. "
            "Subagent work streams to the UI; the runtime auto-waits when the spawn batch ends."
        )

    async def execute(
        self,
        task: str,
        label: str | None = None,
        tools: list[str] | None = None,
        skills: list[str] | None = None,
        context: str | None = None,
        **kwargs: Any,
    ) -> str:
        """启动子代理执行给定任务。"""
        return await self._manager.spawn(
            task=task,
            label=label,
            origin_channel=self._origin_channel,
            origin_chat_id=self._origin_chat_id,
            session_key=self._session_key,
            tools=tools,
            skills=skills,
            context=context,
        )
