"""用于后台任务执行的子代理管理器。"""

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

from loguru import logger

from firefly.agent.hook import AgentHook, AgentHookContext
from firefly.agent.orchestrator import TaskOrchestrator
from firefly.utils.prompt_templates import render_template
from firefly.agent.runner import AgentRunSpec, AgentRunner
from firefly.agent.skills import BUILTIN_SKILLS_DIR
from firefly.agent.tools.filesystem import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from firefly.agent.tools.registry import ToolRegistry
from firefly.agent.tools.search import GlobTool, GrepTool
from firefly.agent.tools.shell import ExecTool
from firefly.agent.tools.web import WebFetchTool, WebSearchTool
from firefly.bus.events import InboundMessage, OutboundMessage
from firefly.bus.queue import MessageBus
from firefly.config.schema import ExecToolConfig, WebToolsConfig
from firefly.providers.base import LLMProvider
from firefly.session.manager import Session, SessionManager
from firefly.utils.tool_hints import format_tool_hints
from firefly.zotero_interface.utils import (
    ZOTERO_SPAWN_CONTEXT_KEY,
    ZOTERO_USER_REQUEST_KEY,
    extract_zotero_spawn_context,
)


class _SubagentHook(AgentHook):
    """子代理执行钩子：向前端推送工具步骤（不推送 assistant 叙述流）。"""

    def __init__(
        self,
        task_id: str,
        label: str,
        bus: MessageBus,
        channel: str,
        chat_id: str,
    ) -> None:
        super().__init__()
        self._task_id = task_id
        self._label = label
        self._bus = bus
        self._channel = channel
        self._chat_id = chat_id

    def wants_streaming(self) -> bool:
        return False

    async def _publish_tool_step(self, content: str) -> None:
        text = content.strip()
        if not text:
            return
        await self._bus.publish_outbound(OutboundMessage(
            channel=self._channel,
            chat_id=self._chat_id,
            content=f"[{self._label}] {text}",
            metadata={
                "_progress": True,
                "_tool_hint": True,
                "_subagent": self._label,
            },
        ))

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        for tool_call in context.tool_calls:
            args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
            logger.debug(
                "Subagent [{}] executing: {} with arguments: {}",
                self._task_id, tool_call.name, args_str,
            )
        hint = format_tool_hints(context.tool_calls)
        await self._publish_tool_step(hint)

    async def on_stream(self, context: AgentHookContext, delta: str) -> None:
        del context, delta

    async def on_reasoning_stream(self, delta: str) -> None:
        del delta


class SubagentManager:
    """管理后台子代理执行。"""

    def __init__(
        self,
        provider: LLMProvider,
        workspace: Path,
        bus: MessageBus,
        max_tool_result_chars: int,
        model: str | None = None,
        web_config: "WebToolsConfig | None" = None,
        exec_config: "ExecToolConfig | None" = None,
        restrict_to_workspace: bool = False,
        disabled_skills: list[str] | None = None,
    ):
        from firefly.config.schema import ExecToolConfig

        self.provider = provider
        self.workspace = workspace
        self.bus = bus
        self.model = model or provider.get_default_model()
        self.web_config = web_config or WebToolsConfig()
        self.max_tool_result_chars = max_tool_result_chars
        self.exec_config = exec_config or ExecToolConfig()
        self.restrict_to_workspace = restrict_to_workspace
        self.disabled_skills = set(disabled_skills or [])
        self.runner = AgentRunner(provider)
        self._running_tasks: dict[str, asyncio.Task[None]] = {}
        self._session_tasks: dict[str, set[str]] = {}  # session_key -> {task_id, ...}
        self._parent_registry: ToolRegistry | None = None
        self._orchestrators: dict[str, TaskOrchestrator] = {}
        self._session_manager: SessionManager | None = None

    def set_session_manager(self, sessions: SessionManager) -> None:
        """绑定会话管理器，供 spawn 时注入 Zotero 运行时上下文。"""
        self._session_manager = sessions

    def _get_orchestrator(self, session_key: str) -> TaskOrchestrator:
        if session_key not in self._orchestrators:
            self._orchestrators[session_key] = TaskOrchestrator(
                result_preview_chars=self.max_tool_result_chars,
            )
        return self._orchestrators[session_key]

    def start_task_plan(self, session_key: str, stages: list[str]) -> str:
        return self._get_orchestrator(session_key).start_plan(stages)

    async def await_task_stage(self, session_key: str) -> str:
        return await self._get_orchestrator(session_key).await_current_stage()

    def format_plan_context(self, session_key: str) -> str | None:
        return self._get_orchestrator(session_key).format_context_block()

    def set_parent_registry(self, registry: ToolRegistry) -> None:
        """绑定主 agent 工具注册表，供 task profile 子集引用（含 MCP）。"""
        self._parent_registry = registry

    async def spawn(
        self,
        task: str,
        label: str | None = None,
        origin_channel: str = "zotero",
        origin_chat_id: str = "direct",
        session_key: str | None = None,
        tools: list[str] | None = None,
        skills: list[str] | None = None,
        context: str | None = None,
    ) -> str:
        """启动子代理在后台执行任务。"""
        if not tools:
            return (
                "Error: spawn requires `tools`. "
                "Example: tools=[\"rag_search\", \"read_file\", \"grep\"]. "
                "See Delegation catalog / common spawn profiles in the system prompt."
            )
        if not skills:
            return (
                "Error: spawn requires `skills`. "
                "Example: skills=[\"markdown\", \"zotero\"]."
            )
        err = self._validate_profile_tools(tools)
        if err:
            return err
        err = self._validate_profile_skills(skills)
        if err:
            return err
        if "zotero" in skills and "rag_search" not in tools:
            return (
                "Error: spawn with zotero skill must include rag_search in tools=. "
                "Literature extraction profile: tools=[\"rag_search\", \"read_file\", \"grep\"], "
                "skills=[\"markdown\", \"zotero\"]."
            )
        err = self._validate_docx_spawn_tools(tools, skills)
        if err:
            return err

        user_content, ctx_err = self._compose_subagent_user_content(
            task=task,
            context=context,
            session_key=session_key,
        )
        if ctx_err:
            return ctx_err

        task_id = str(uuid.uuid4())[:8]
        display_label = label or task[:30] + ("..." if len(task) > 30 else "")
        origin = {"channel": origin_channel, "chat_id": origin_chat_id}

        defer_announce = False
        if session_key:
            orch = self._get_orchestrator(session_key)
            if orch.is_active:
                defer_announce = True
                orch.register_subtask(task_id, display_label, task)

        bg_task = asyncio.create_task(
            self._run_subagent(
                task_id, user_content, display_label, origin,
                tool_names=tools, skill_names=skills,
                session_key=session_key,
                defer_announce=defer_announce,
            )
        )
        self._running_tasks[task_id] = bg_task
        if session_key:
            self._session_tasks.setdefault(session_key, set()).add(task_id)

        if defer_announce:
            await self._publish_tool_step(
                origin,
                f"子任务启动: {display_label}",
            )

        def _cleanup(_: asyncio.Task) -> None:
            self._running_tasks.pop(task_id, None)
            if session_key and (ids := self._session_tasks.get(session_key)):
                ids.discard(task_id)
                if not ids:
                    del self._session_tasks[session_key]

        bg_task.add_done_callback(_cleanup)

        logger.info("Spawned subagent [{}]: {}", task_id, display_label)
        if defer_announce and session_key:
            orch = self._get_orchestrator(session_key)
            return (
                f"Subagent [{display_label}] started (id: {task_id}). "
                f"Stage pending: {orch.running_count()} running / "
                f"{len(orch.pending)} total. "
                "The runtime will auto-wait for this stage when dispatch finishes."
            )
        return f"Subagent [{display_label}] started (id: {task_id}). I'll notify you when it completes."

    async def _run_subagent(
        self,
        task_id: str,
        user_content: str,
        label: str,
        origin: dict[str, str],
        *,
        tool_names: list[str] | None = None,
        skill_names: list[str] | None = None,
        session_key: str | None = None,
        defer_announce: bool = False,
    ) -> None:
        """执行子代理任务并广播结果。"""
        logger.info("Subagent [{}] starting task: {}", task_id, label)

        try:
            tools, tools_err = self._build_tools(tool_names)
            if tools_err:
                await self._finish_subtask(
                    task_id, label, user_content, tools_err, origin, session_key,
                    ok=False, defer_announce=defer_announce,
                )
                return

            system_prompt = self._build_subagent_prompt(
                skill_names=skill_names, tool_names=tool_names,
            )
            messages: list[dict[str, Any]] = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ]

            hook = _SubagentHook(
                task_id,
                label,
                self.bus,
                origin["channel"],
                origin["chat_id"],
            )

            result = await self.runner.run(AgentRunSpec(
                initial_messages=messages,
                tools=tools,
                model=self.model,
                max_iterations=25,
                max_tool_result_chars=self.max_tool_result_chars,
                hook=hook,
                max_iterations_message="Task completed but no final response was generated.",
                error_message=None,
                fail_on_tool_error=True,
                concurrent_tools=True,
                session_key=session_key,
                on_reasoning_stream=None,
            ))
            if result.stop_reason == "tool_error":
                await self._finish_subtask(
                    task_id, label, user_content,
                    self._format_partial_progress(result),
                    origin, session_key, ok=False, defer_announce=defer_announce,
                )
                return
            if result.stop_reason == "error":
                await self._finish_subtask(
                    task_id, label, user_content,
                    result.error or "Error: subagent execution failed.",
                    origin, session_key, ok=False, defer_announce=defer_announce,
                )
                return
            final_result = result.final_content or "Task completed but no final response was generated."

            logger.info("Subagent [{}] completed successfully", task_id)
            await self._finish_subtask(
                task_id, label, user_content, final_result, origin, session_key,
                ok=True, defer_announce=defer_announce,
            )

        except Exception as e:
            error_msg = f"Error: {str(e)}"
            logger.error("Subagent [{}] failed: {}", task_id, e)
            await self._finish_subtask(
                task_id, label, user_content, error_msg, origin, session_key,
                ok=False, defer_announce=defer_announce,
            )

    async def _publish_tool_step(self, origin: dict[str, str], content: str) -> None:
        text = content.strip()
        if not text:
            return
        await self.bus.publish_outbound(OutboundMessage(
            channel=origin["channel"],
            chat_id=origin["chat_id"],
            content=text,
            metadata={"_progress": True, "_tool_hint": True},
        ))

    async def _finish_subtask(
        self,
        task_id: str,
        label: str,
        task: str,
        result: str,
        origin: dict[str, str],
        session_key: str | None,
        *,
        ok: bool,
        defer_announce: bool,
    ) -> None:
        if defer_announce:
            mark = "完成" if ok else "失败"
            await self._publish_tool_step(origin, f"子任务{mark}: {label}")
        if defer_announce and session_key:
            orch = self._get_orchestrator(session_key)
            if orch.is_active or task_id in orch.pending:
                orch.complete_subtask(task_id, result, ok=ok)
                return
        await self._announce_result(
            task_id, label, task, result, origin, "ok" if ok else "error",
        )

    async def _announce_result(
        self,
        task_id: str,
        label: str,
        task: str,
        result: str,
        origin: dict[str, str],
        status: str,
    ) -> None:
        """通过消息总线将子代理结果通知主代理。"""
        status_text = "completed successfully" if status == "ok" else "failed"

        announce_content = render_template(
            "agent/subagent_announce.md",
            label=label,
            status_text=status_text,
            task=task,
            result=result,
        )

        # 以 system 消息注入，触发主代理处理
        msg = InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id=f"{origin['channel']}:{origin['chat_id']}",
            content=announce_content,
        )

        await self.bus.publish_inbound(msg)
        logger.debug("Subagent [{}] announced result to {}:{}", task_id, origin['channel'], origin['chat_id'])

    @staticmethod
    def _format_partial_progress(result) -> str:
        completed = [e for e in result.tool_events if e["status"] == "ok"]
        failure = next((e for e in reversed(result.tool_events) if e["status"] == "error"), None)
        lines: list[str] = []
        if completed:
            lines.append("Completed steps:")
            for event in completed[-3:]:
                lines.append(f"- {event['name']}: {event['detail']}")
        if failure:
            if lines:
                lines.append("")
            lines.append("Failure:")
            lines.append(f"- {failure['name']}: {failure['detail']}")
        if result.error and not failure:
            if lines:
                lines.append("")
            lines.append("Failure:")
            lines.append(f"- {result.error}")
        return "\n".join(lines) or (result.error or "Error: subagent execution failed.")

    def _compose_subagent_user_content(
        self,
        *,
        task: str,
        context: str | None,
        session_key: str | None,
    ) -> tuple[str, str | None]:
        """组装子 agent 用户消息：会话 Zotero 上下文 + 主 agent 委派上下文 + 前序阶段结果 + 任务。"""
        task = task.strip()
        if not task:
            return "", "Error: spawn `task` cannot be empty."

        parts: list[str] = []
        session_ctx = ""
        user_request = ""
        prior_stages = ""

        if session_key and self._session_manager is not None:
            session = self._session_manager.get_or_create(session_key)
            raw_ctx = session.metadata.get(ZOTERO_SPAWN_CONTEXT_KEY)
            if isinstance(raw_ctx, str) and raw_ctx.strip():
                session_ctx = raw_ctx.strip()
            raw_req = session.metadata.get(ZOTERO_USER_REQUEST_KEY)
            if isinstance(raw_req, str) and raw_req.strip():
                user_request = raw_req.strip()
            if not user_request:
                for msg in reversed(session.messages):
                    if msg.get("role") != "user":
                        continue
                    content = str(msg.get("content") or "").strip()
                    if content:
                        user_request = content
                        lit = msg.get("literature_title")
                        if isinstance(lit, str) and lit.strip():
                            user_request = f"[literature_title={lit.strip()}]\n{user_request}"
                        break

        if session_key:
            orch = self._orchestrators.get(session_key)
            if orch and orch.completed_stage_results:
                prior_stages = "\n\n".join(orch.completed_stage_results).strip()

        delegated = context.strip() if isinstance(context, str) else ""

        if session_ctx:
            parts.append(f"[Zotero Runtime Context]\n{session_ctx}")
        if prior_stages:
            parts.append(f"[Prior Stage Results]\n{prior_stages}")
        if delegated:
            parts.append(f"[Delegated Context]\n{delegated}")
        if user_request:
            parts.append(f"[Original User Request]\n{user_request}")
        parts.append(f"[Task]\n{task}")

        if not session_ctx and not delegated and not prior_stages:
            return "", (
                "Error: spawn requires execution context. "
                "Pass `context=` with markdown paths, prior-stage outputs, and any "
                "facts the subagent needs (or ensure the user turn was persisted with "
                "Zotero path markers)."
            )

        return "\n\n".join(parts), None

    @staticmethod
    def persist_zotero_spawn_context(
        session: Session,
        *,
        raw_user_content: str,
        stored_user_request: str,
        literature_title: str | None = None,
    ) -> None:
        """在用户轮次落盘时保存子 agent 可复用的 Zotero 运行时上下文。"""
        spawn_ctx = extract_zotero_spawn_context(raw_user_content)
        if literature_title and literature_title.strip():
            title_line = f"[literature_title={literature_title.strip()}]"
            if title_line not in spawn_ctx:
                spawn_ctx = f"{title_line}\n{spawn_ctx}".strip() if spawn_ctx else title_line
        session.metadata[ZOTERO_SPAWN_CONTEXT_KEY] = spawn_ctx
        session.metadata[ZOTERO_USER_REQUEST_KEY] = stored_user_request.strip()

    def _build_subagent_prompt(
        self,
        *,
        skill_names: list[str] | None = None,
        tool_names: list[str] | None = None,
    ) -> str:
        """为子代理构建聚焦的系统提示词。"""
        from firefly.agent.context import ContextBuilder
        from firefly.agent.skills import SkillsLoader

        time_ctx = ContextBuilder._build_runtime_context(None, None)
        loader = SkillsLoader(self.workspace, disabled_skills=self.disabled_skills)

        skills_content = ""
        skills_summary = ""
        if skill_names:
            skills_content = loader.load_skills_for_context(skill_names)
        else:
            skills_summary = loader.build_skills_summary()

        allowed_tools = ""
        if tool_names:
            allowed_tools = "\n".join(f"- `{name}`" for name in tool_names)

        return render_template(
            "agent/subagent_system.md",
            time_ctx=time_ctx,
            workspace=str(self.workspace),
            skills_summary=skills_summary or "",
            skills_content=skills_content or "",
            allowed_tools=allowed_tools,
        )

    def _build_default_tools(self) -> ToolRegistry:
        """默认子代理工具集（filesystem / search / exec / web）。"""
        tools = ToolRegistry()
        allowed_dir = self.workspace if (self.restrict_to_workspace or self.exec_config.sandbox) else None
        extra_read = [BUILTIN_SKILLS_DIR] if allowed_dir else None
        tools.register(ReadFileTool(workspace=self.workspace, allowed_dir=allowed_dir, extra_allowed_dirs=extra_read))
        tools.register(WriteFileTool(workspace=self.workspace, allowed_dir=allowed_dir))
        tools.register(EditFileTool(workspace=self.workspace, allowed_dir=allowed_dir))
        tools.register(ListDirTool(workspace=self.workspace, allowed_dir=allowed_dir))
        tools.register(GlobTool(workspace=self.workspace, allowed_dir=allowed_dir))
        tools.register(GrepTool(workspace=self.workspace, allowed_dir=allowed_dir))
        if self.exec_config.enable:
            tools.register(ExecTool(
                working_dir=str(self.workspace),
                timeout=self.exec_config.timeout,
                restrict_to_workspace=self.restrict_to_workspace,
                sandbox=self.exec_config.sandbox,
                path_append=self.exec_config.path_append,
            ))
        if self.web_config.enable:
            tools.register(WebSearchTool(config=self.web_config.search, proxy=self.web_config.proxy))
            tools.register(WebFetchTool(proxy=self.web_config.proxy))
        return tools

    def _build_tools(self, tool_names: list[str] | None) -> tuple[ToolRegistry, str | None]:
        """按 task profile 构建工具集；无 tool_names 时用默认子集。"""
        if tool_names is None:
            return self._build_default_tools(), None
        if self._parent_registry is None:
            return ToolRegistry(), (
                "Error: subagent task profile requires parent tool registry "
                "(not configured)."
            )
        registry, missing = self._parent_registry.subset(tool_names)
        if missing:
            available = ", ".join(sorted(self._parent_registry.tool_names))
            return registry, (
                f"Error: unknown tools for subagent profile: {', '.join(missing)}. "
                f"Available: {available}"
            )
        return registry, None

    def _validate_docx_spawn_tools(self, tool_names: list[str], skill_names: list[str]) -> str | None:
        if "docx" not in skill_names:
            return None
        names = set(tool_names)
        if not any("create_from_markdown" in t for t in names):
            return None
        required = {
            "mcp_docx-mcp_open_document",
            "mcp_docx-mcp_get_document_info",
            "mcp_docx-mcp_get_headings",
            "mcp_docx-mcp_search_text",
        }
        missing = sorted(required - names)
        if missing:
            return (
                "Error: docx create spawn must include verify tools: "
                f"{', '.join(missing)}. "
                "Profile: tools=[create_from_markdown, open_document, get_document_info, "
                "get_headings, search_text] (+ save_document only if editing after open)."
            )
        return None

    def _validate_profile_tools(self, tool_names: list[str]) -> str | None:
        if self._parent_registry is None:
            return (
                "Error: spawn `tools` requires the main agent tool registry "
                "(not configured)."
            )
        _, missing = self._parent_registry.subset(tool_names)
        if missing:
            available = ", ".join(sorted(self._parent_registry.tool_names))
            return (
                f"Error: unknown tools: {', '.join(missing)}. Available: {available}"
            )
        blocked = {"spawn", "message", "cron"} & set(tool_names)
        if blocked:
            return f"Error: subagents cannot use: {', '.join(sorted(blocked))}"
        return None

    def _validate_profile_skills(self, skill_names: list[str]) -> str | None:
        from firefly.agent.skills import SkillsLoader

        loader = SkillsLoader(self.workspace, disabled_skills=self.disabled_skills)
        missing = [name for name in skill_names if loader.load_skill(name) is None]
        if missing:
            return f"Error: unknown skills: {', '.join(missing)}"
        return None

    async def cancel_by_session(self, session_key: str) -> int:
        """取消指定会话下所有子代理，返回取消数量。"""
        tasks = [self._running_tasks[tid] for tid in self._session_tasks.get(session_key, [])
                 if tid in self._running_tasks and not self._running_tasks[tid].done()]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        orch = self._orchestrators.get(session_key)
        if orch:
            for entry in orch.pending.values():
                if entry.status == "running":
                    entry.status = "error"
                    entry.result = "cancelled"
            orch._wake.set()
        return len(tasks)

    def get_running_count(self) -> int:
        """返回当前运行中的子代理数量。"""
        return len(self._running_tasks)
