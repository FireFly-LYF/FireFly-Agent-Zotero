"""用于创建后台子代理的 spawn 工具。"""

from typing import TYPE_CHECKING, Any

from firefly.agent.tools.base import Tool, tool_parameters
from firefly.agent.tools.schema import ArraySchema, StringSchema, tool_parameters_schema

if TYPE_CHECKING:
    from firefly.agent.subagent import SubagentManager


@tool_parameters(
    tool_parameters_schema(
        task=StringSchema(
            "Deliverable for this stage — what the subagent must **return**, not how. "
            "Do NOT name tools (get_markdown_headings, rag_search, read_file, …) or list steps. "
            "Put markdown paths and prior-stage output in `context`. "
            "Good (literature): '从论文 markdown 提取抗干扰方法步骤与关键公式，返回结构化笔记'. "
            "Good (docx): '将阶段1笔记写入 docx/xxx.docx 并验证标题与公式'. "
            "Bad: '先用 get_markdown_headings 读标题，再 rag_search…'."
        ),
        label=StringSchema(
            "Optional; ignored for UI. Tool steps show as [spawn 1], [spawn 2], … automatically."
        ),
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
                "Literature stage: [\"rag_search\", \"get_markdown_headings\", \"read_file\"]. Required on every spawn."
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
            "Delegate one stage's work to subagent(s). Multiple parallel spawns are OK "
            "when subtasks are independent. Pass outcome-focused `task`, plus `tools`, "
            "`skills`, and `context`. "
            "Do not spawn separate subagents for headings/RAG/read inside the same literature stage."
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
