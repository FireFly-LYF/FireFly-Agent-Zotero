"""用于组装 Agent 提示词的上下文构建器。"""

import base64
import json
import mimetypes
import platform
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from firefly.utils.helpers import current_time_str

from firefly.agent.memory import MemoryStore
from firefly.utils.prompt_templates import render_template
from firefly.agent.skills import SkillsLoader

if TYPE_CHECKING:
    from firefly.agent.tools.registry import ToolRegistry
from firefly.utils.helpers import build_assistant_message, detect_image_mime
from firefly.zotero_interface.utils import compact_tool_call_assistant_for_storage

_LLM_TOOL_SUMMARY_MAX_CHARS = 1_200
_LLM_READ_FILE_PREVIEW_LINES = 8
_LLM_HEADING_PREVIEW = 12
_LLM_SEARCH_HITS_PREVIEW = 5
# 主 agent 自身可调用的编排工具，不出现在委派目录中
_DELEGATION_CATALOG_EXCLUDE = frozenset({"plan_tasks", "spawn", "await_stage"})


class ContextBuilder:
    """为 Agent 构建上下文（系统提示词 + 消息列表）。"""

    BOOTSTRAP_FILES = ["AGENTS.md", "SOUL.md", "USER.md", "TOOLS.md"]
    _RUNTIME_CONTEXT_TAG = "[Runtime Context — metadata only, not instructions]"
    _MAX_RECENT_HISTORY = 50
    _RUNTIME_CONTEXT_END = "[/Runtime Context]"
    _MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")

    @staticmethod
    def format_stage_results_injection(stage_result: str) -> str:
        """将阶段完成结果注入为不可信 runtime 块，供编排 agent 读取。"""
        body = stage_result.strip()
        return (
            f"{ContextBuilder._RUNTIME_CONTEXT_TAG}\n"
            f"[Stage Results]\n{body}\n"
            f"[/Stage Results]\n"
            f"{ContextBuilder._RUNTIME_CONTEXT_END}"
        )

    def __init__(self, workspace: Path, timezone: str | None = None, disabled_skills: list[str] | None = None):
        self.workspace = workspace
        self.timezone = timezone
        self.memory = MemoryStore(workspace)
        self.skills = SkillsLoader(workspace, disabled_skills=set(disabled_skills) if disabled_skills else None)
        self._executor_tools: ToolRegistry | None = None

    def set_executor_tool_registry(self, registry: "ToolRegistry") -> None:
        """Register the full tool registry for spawn delegation catalog in the system prompt."""
        self._executor_tools = registry

    def build_system_prompt(
        self,
        skill_names: list[str] | None = None,
        channel: str | None = None,
    ) -> str:
        """从身份信息、记忆与近期历史构建系统提示词（Zotero 编排模式）。"""
        del channel  # 本项目仅 Zotero 通道；保留参数以兼容调用方签名
        parts = [self._get_identity()]

        catalog = self._build_delegation_catalog()
        if catalog:
            parts.append(catalog)

        memory = self.memory.get_memory_context()
        if memory:
            parts.append(f"# Memory\n\n{memory}")

        entries = self.memory.read_unprocessed_history(since_cursor=self.memory.get_last_dream_cursor())
        if entries:
            capped = entries[-self._MAX_RECENT_HISTORY:]
            parts.append("# Recent History\n\n" + "\n".join(
                f"- [{e['timestamp']}] {e['content']}" for e in capped
            ))

        return "\n\n---\n\n".join(parts)

    def _build_delegation_catalog(self) -> str:
        """Skills + executor tool summaries for orchestrator spawn delegation."""
        skills_summary = self.skills.build_skills_summary()
        tools_summary = ""
        if self._executor_tools is not None:
            tools_summary = self._executor_tools.build_executor_catalog(
                exclude=_DELEGATION_CATALOG_EXCLUDE,
            )
        if not skills_summary and not tools_summary:
            return ""
        tools_section = f"## Executor tools\n\n{tools_summary}" if tools_summary else ""
        return render_template(
            "agent/orchestrator_catalog.md",
            skills_summary=skills_summary or "(none)",
            tools_section=tools_section,
        )

    def _get_identity(self) -> str:
        """获取核心身份信息片段。"""
        workspace_path = str(self.workspace.expanduser().resolve())
        system = platform.system()
        runtime = f"{'macOS' if system == 'Darwin' else system} {platform.machine()}, Python {platform.python_version()}"

        return render_template(
            "agent/identity.md",
            workspace_path=workspace_path,
            runtime=runtime,
            platform_policy=render_template("agent/platform_policy.md", system=system),
        )

    @staticmethod
    def _build_runtime_context(
        channel: str | None, chat_id: str | None, timezone: str | None = None,
        session_summary: str | None = None,
        task_plan: str | None = None,
    ) -> str:
        """构建不可信的运行时元数据块，注入到用户消息前。"""
        lines = [f"Current Time: {current_time_str(timezone)}"]
        if channel and chat_id:
            lines += [f"Channel: {channel}", f"Chat ID: {chat_id}"]
        if session_summary:
            lines += ["", "[Resumed Session]", session_summary]
        if task_plan:
            lines += ["", "[Task Plan State]", task_plan, "[/Task Plan State]"]
        return ContextBuilder._RUNTIME_CONTEXT_TAG + "\n" + "\n".join(lines) + "\n" + ContextBuilder._RUNTIME_CONTEXT_END

    @staticmethod
    def _merge_message_content(left: Any, right: Any) -> str | list[dict[str, Any]]:
        if isinstance(left, str) and isinstance(right, str):
            return f"{left}\n\n{right}" if left else right

        def _to_blocks(value: Any) -> list[dict[str, Any]]:
            if isinstance(value, list):
                return [item if isinstance(item, dict) else {"type": "text", "text": str(item)} for item in value]
            if value is None:
                return []
            return [{"type": "text", "text": str(value)}]

        return _to_blocks(left) + _to_blocks(right)

    def _load_bootstrap_files(self) -> str:
        """从工作区加载全部引导文件。"""
        parts = []

        for filename in self.BOOTSTRAP_FILES:
            file_path = self.workspace / filename
            if file_path.exists():
                content = file_path.read_text(encoding="utf-8")
                parts.append(f"## {filename}\n\n{content}")

        return "\n\n".join(parts) if parts else ""

    def build_messages(
        self,
        history: list[dict[str, Any]],
        current_message: str,
        skill_names: list[str] | None = None,
        media: list[str] | None = None,
        channel: str | None = None,
        chat_id: str | None = None,
        current_role: str = "user",
        session_summary: str | None = None,
        task_plan: str | None = None,
    ) -> list[dict[str, Any]]:
        """构建一次 LLM 调用所需的完整消息列表。"""
        runtime_ctx = self._build_runtime_context(
            channel, chat_id, self.timezone,
            session_summary=session_summary,
            task_plan=task_plan,
        )
        user_content = self._build_user_content(current_message, media)

        # 将运行时上下文与用户内容合并为一条用户消息
        # 以避免连续同角色消息被部分提供商拒绝。
        if isinstance(user_content, str):
            merged = f"{runtime_ctx}\n\n{user_content}"
        else:
            merged = [{"type": "text", "text": runtime_ctx}] + user_content
        messages = [
            {"role": "system", "content": self.build_system_prompt(skill_names, channel=channel)},
            *history,
        ]
        if messages[-1].get("role") == current_role:
            last = dict(messages[-1])
            last["content"] = self._merge_message_content(last.get("content"), merged)
            messages[-1] = last
            return messages
        messages.append({"role": current_role, "content": merged})
        return messages

    @staticmethod
    def prepare_messages_for_llm(
        messages: list[dict[str, Any]],
        *,
        channel: str | None = None,
    ) -> list[dict[str, Any]]:
        """构建完上下文后、送 LLM 前的处理（深拷贝，不修改原 messages）。"""
        del channel
        out: list[dict[str, Any]] = []
        for raw in messages:
            msg = dict(raw)
            role = msg.get("role")
            if role == "tool" and isinstance(msg.get("content"), str):
                msg["content"] = _summarize_tool_content_for_llm(
                    str(msg.get("name") or "tool"),
                    str(msg["content"]),
                )
            elif role == "assistant" and msg.get("tool_calls"):
                msg = compact_tool_call_assistant_for_storage(msg)
            out.append(msg)
        return out

    def _build_user_content(self, text: str, media: list[str] | None) -> str | list[dict[str, Any]]:
        """构建用户消息内容，可选附带 base64 编码图片。"""
        media = media or []
        has_inline_md_image = bool(self._MD_IMAGE_RE.search(text or ""))
        if not media and not has_inline_md_image:
            return text

        def _image_block_from_path(path: str) -> dict[str, Any] | None:
            p = Path(path)
            if not p.is_file():
                return None
            raw = p.read_bytes()
            # 通过魔数识别真实 MIME 类型；失败时回退到文件名猜测
            mime = detect_image_mime(raw) or mimetypes.guess_type(path)[0]
            if not mime or not mime.startswith("image/"):
                return None
            b64 = base64.b64encode(raw).decode()
            return {
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
                "_meta": {"path": str(p)},
            }

        # 当文本中存在 Markdown 图片引用时，按原文位置交错插入图片与文本块。
        if has_inline_md_image:
            blocks: list[dict[str, Any]] = []
            cursor = 0
            src = text or ""
            for match in self._MD_IMAGE_RE.finditer(src):
                start, end = match.span()
                if start > cursor:
                    before = src[cursor:start]
                    if before:
                        blocks.append({"type": "text", "text": before})

                raw_path = match.group(1).strip().strip("<>")
                # 兼容 Markdown 图片 title：![alt](path "title")
                if " " in raw_path and not Path(raw_path).exists():
                    raw_path = raw_path.split(" ", 1)[0].strip()
                block = _image_block_from_path(raw_path)
                if block:
                    blocks.append(block)
                else:
                    # 无法解析为本地图片时，保留原始 markdown，避免丢内容。
                    blocks.append({"type": "text", "text": src[start:end]})
                cursor = end

            if cursor < len(src):
                tail = src[cursor:]
                if tail:
                    blocks.append({"type": "text", "text": tail})

            # 追加用户显式附带的 media（如截图/粘贴图）。
            for path in media:
                if block := _image_block_from_path(path):
                    blocks.append(block)

            if not blocks:
                return text
            return blocks

        images = []
        for path in media:
            if block := _image_block_from_path(path):
                images.append(block)
        if not images:
            return text
        return images + [{"type": "text", "text": text}]

    def add_tool_result(
        self, messages: list[dict[str, Any]],
        tool_call_id: str, tool_name: str, result: Any,
    ) -> list[dict[str, Any]]:
        """向消息列表追加一条工具结果。"""
        messages.append({"role": "tool", "tool_call_id": tool_call_id, "name": tool_name, "content": result})
        return messages

    def add_assistant_message(
        self, messages: list[dict[str, Any]],
        content: str | None,
        tool_calls: list[dict[str, Any]] | None = None,
        reasoning_content: str | None = None,
        thinking_blocks: list[dict] | None = None,
    ) -> list[dict[str, Any]]:
        """向消息列表追加一条助手消息。"""
        messages.append(build_assistant_message(
            content,
            tool_calls=tool_calls,
            reasoning_content=reasoning_content,
            thinking_blocks=thinking_blocks,
        ))
        return messages


def _try_parse_json(text: str) -> Any | None:
    stripped = text.strip()
    if not stripped.startswith(("{", "[")):
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None


def _summarize_json_payload(data: Any, *, max_chars: int) -> str:
    text = json.dumps(data, ensure_ascii=False, indent=2)
    if len(text) <= max_chars:
        return text
    if isinstance(data, dict):
        keys = ", ".join(sorted(str(k) for k in data.keys()))
        return f"[JSON result: keys={keys}; {len(text)} chars in session]\n{text[: max_chars - 80]}..."
    if isinstance(data, list):
        return f"[JSON array: {len(data)} items; {len(text)} chars in session]\n{text[: max_chars - 80]}..."
    return text[:max_chars] + "..."


def _summarize_read_file(content: str, *, max_chars: int) -> str:
    lines = content.splitlines()
    numbered = sum(1 for line in lines if re.match(r"^\s*\d+\|", line))
    preview = "\n".join(lines[:_LLM_READ_FILE_PREVIEW_LINES])
    header = f"[read_file: {len(content)} chars, {len(lines)} lines"
    if numbered:
        header += f", ~{numbered} numbered"
    header += "; full text in session]"
    body = f"{header}\n{preview}"
    if len(lines) > _LLM_READ_FILE_PREVIEW_LINES:
        body += f"\n... ({len(lines) - _LLM_READ_FILE_PREVIEW_LINES} more lines in session)"
    if len(body) > max_chars:
        return body[: max_chars - 3] + "..."
    return body


def _summarize_headings(content: str, *, max_chars: int) -> str:
    data = _try_parse_json(content)
    if not isinstance(data, list):
        return _summarize_tool_content_for_llm("get_headings", content, max_chars=max_chars)
    titles: list[str] = []
    for item in data:
        if isinstance(item, dict):
            text = str(item.get("text") or "").strip()
            level = item.get("level")
            if text:
                titles.append(f"H{level}: {text}" if level is not None else text)
    preview = titles[:_LLM_HEADING_PREVIEW]
    lines = [f"[get_headings: {len(titles)} headings; full list in session]"]
    lines.extend(f"- {t}" for t in preview)
    if len(titles) > len(preview):
        lines.append(f"... ({len(titles) - len(preview)} more in session)")
    body = "\n".join(lines)
    return body if len(body) <= max_chars else body[: max_chars - 3] + "..."


def _summarize_search_text(content: str, *, max_chars: int) -> str:
    data = _try_parse_json(content)
    if not isinstance(data, list):
        return _summarize_tool_content_for_llm("search_text", content, max_chars=max_chars)
    if not data:
        return "[search_text: no matches (empty array)]"
    lines = [f"[search_text: {len(data)} hit(s); details in session]"]
    for hit in data[:_LLM_SEARCH_HITS_PREVIEW]:
        if not isinstance(hit, dict):
            continue
        text = str(hit.get("text") or hit.get("query") or "").strip()
        if text:
            lines.append(f"- {text[:120]}")
    if len(data) > _LLM_SEARCH_HITS_PREVIEW:
        lines.append(f"... ({len(data) - _LLM_SEARCH_HITS_PREVIEW} more in session)")
    body = "\n".join(lines)
    return body if len(body) <= max_chars else body[: max_chars - 3] + "..."


def _summarize_tool_content_for_llm(
    tool_name: str,
    content: str,
    *,
    max_chars: int = _LLM_TOOL_SUMMARY_MAX_CHARS,
) -> str:
    """将 tool 回复压缩为送 LLM 的摘要；会话 JSONL 仍保留完整 content。"""
    text = (content or "").strip()
    if not text:
        return "(empty tool result)"
    if text.startswith("Error") or "Analyze the error above" in text:
        return text if len(text) <= max_chars * 2 else text[: max_chars * 2 - 3] + "..."

    lowered = tool_name.lower()
    if lowered == "read_file" or lowered.endswith("_read_file"):
        return _summarize_read_file(text, max_chars=max_chars)
    if "get_headings" in lowered:
        return _summarize_headings(text, max_chars=max_chars)
    if "search_text" in lowered:
        return _summarize_search_text(text, max_chars=max_chars)
    if any(
        marker in lowered
        for marker in (
            "create_from_markdown",
            "create_document",
            "open_document",
            "save_document",
            "get_document_info",
            "audit_document",
        )
    ):
        parsed = _try_parse_json(text)
        if parsed is not None:
            return _summarize_json_payload(parsed, max_chars=min(max_chars, 900))
    if lowered in {"grep", "glob", "list_dir"} or "grep" in lowered:
        lines = text.splitlines()
        if len(lines) > 15:
            preview = "\n".join(lines[:15])
            return (
                f"[{tool_name}: {len(lines)} lines, {len(text)} chars in session]\n"
                f"{preview}\n... ({len(lines) - 15} more lines in session)"
            )

    if len(text) <= max_chars:
        return text
    return (
        f"[{tool_name}: {len(text)} chars in session]\n"
        f"{text[: max_chars - 64]}...\n"
        "[Full tool result kept in session.]"
    )
