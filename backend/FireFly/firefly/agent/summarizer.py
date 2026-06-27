"""Zotero 流式对话：整轮 agent 任务（含全部工具迭代）结束后，生成一次用户可见回复。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger

from firefly.providers.base import LLMProvider
from firefly.utils.helpers import strip_think
from firefly.utils.prompt_templates import render_template
from firefly.zotero_interface.utils import strip_zotero_user_content_for_session_storage

_MAX_USER_QUERY_CHARS = 4_000
_MAX_DRAFT_CHARS = 12_000
_MAX_TOOL_EVENTS = 12
_MAX_TOOL_DETAIL_CHARS = 240


def _format_tool_log(tool_events: list[dict[str, str]]) -> str:
    if not tool_events:
        return "(no tools used)"
    lines: list[str] = []
    for event in tool_events[-_MAX_TOOL_EVENTS:]:
        name = str(event.get("name") or "tool").strip()
        status = str(event.get("status") or "ok").strip()
        detail = str(event.get("detail") or "").strip()
        if len(detail) > _MAX_TOOL_DETAIL_CHARS:
            detail = detail[: _MAX_TOOL_DETAIL_CHARS - 3] + "..."
        lines.append(f"- {name} [{status}]: {detail or '(ok)'}")
    return "\n".join(lines)


def build_summarizer_user_prompt(
    *,
    user_query: str,
    tool_events: list[dict[str, str]],
    agent_draft: str,
) -> str:
    """构建总结 agent 的用户消息（不含会话历史）。"""
    query = (user_query or "").strip()
    if len(query) > _MAX_USER_QUERY_CHARS:
        query = query[: _MAX_USER_QUERY_CHARS - 3] + "..."

    draft = strip_think(agent_draft or "").strip()
    if len(draft) > _MAX_DRAFT_CHARS:
        draft = draft[: _MAX_DRAFT_CHARS - 3] + "..."

    tool_log = _format_tool_log(tool_events)
    return (
        f"## User question\n{query or '(empty)'}\n\n"
        f"## Work log (tools)\n{tool_log}\n\n"
        f"## Agent draft (internal)\n{draft or '(no draft)'}"
    )


def collect_zotero_turn_user_query(
    initial_content: str,
    all_messages: list[dict[str, Any]],
    *,
    had_injections: bool = False,
) -> str:
    """收集本轮用户问题；中途注入的 follow-up 会追加到同一任务上下文中。"""
    parts: list[str] = []
    seen: set[str] = set()

    def _append(raw: str) -> None:
        text = strip_zotero_user_content_for_session_storage(raw).strip() or raw.strip()
        if text and text not in seen:
            parts.append(text)
            seen.add(text)

    _append(initial_content)
    if had_injections:
        for msg in all_messages:
            if msg.get("role") != "user":
                continue
            _append(str(msg.get("content") or ""))
    return "\n\n".join(parts)


async def finalize_zotero_stream_reply(
    provider: LLMProvider,
    model: str,
    *,
    user_query: str,
    tool_events: list[dict[str, str]],
    agent_draft: str,
    on_answer_stream: Callable[[str], Awaitable[None]] | None = None,
    on_stream_end: Callable[..., Awaitable[None]] | None = None,
) -> str:
    """Agent 任务（含全部工具轮次）结束后，生成一次用户可见回复。

    - 使用了工具：调用无工具的 summarizer 总结整轮工作。
    - 未使用工具：直接将 agent 最终回复流式输出，不再额外调用 LLM。
    """
    if on_stream_end is not None:
        await on_stream_end(resuming=False)

    draft = strip_think(agent_draft or "").strip()
    if tool_events:
        summarized = await summarize_zotero_reply(
            provider,
            model,
            user_query=user_query,
            tool_events=tool_events,
            agent_draft=agent_draft,
            on_stream=on_answer_stream,
        )
        return summarized.strip() or draft or "(无输出)"

    text = draft or "(无输出)"
    if on_answer_stream is not None and text:
        await on_answer_stream(text)
    return text


async def summarize_zotero_reply(
    provider: LLMProvider,
    model: str,
    *,
    user_query: str,
    tool_events: list[dict[str, str]],
    agent_draft: str,
    on_stream: Callable[[str], Awaitable[None]] | None = None,
) -> str:
    """调用无工具的轻量 LLM 轮次，生成用户可见的最终回复。"""
    system_prompt = render_template("agent/reply_summarizer_system.md")
    user_prompt = build_summarizer_user_prompt(
        user_query=user_query,
        tool_events=tool_events,
        agent_draft=agent_draft,
    )
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    logger.info(
        "Zotero reply summarizer: model={} query_chars={} draft_chars={} tools={}",
        model,
        len(user_query or ""),
        len(agent_draft or ""),
        len(tool_events),
    )

    if on_stream is not None:
        parts: list[str] = []

        async def _collect(delta: str) -> None:
            if delta:
                parts.append(delta)
                await on_stream(delta)

        response = await provider.chat_stream_with_retry(
            messages=messages,
            model=model,
            on_content_delta=_collect,
            reasoning_effort=None,
        )
        text = "".join(parts).strip() or strip_think(response.content or "").strip()
    else:
        response = await provider.chat_with_retry(
            messages=messages,
            model=model,
            reasoning_effort=None,
        )
        text = strip_think(response.content or "").strip()

    return text or strip_think(agent_draft or "").strip() or "(无输出)"


def replace_last_assistant_text(messages: list[dict[str, Any]], text: str) -> None:
    """将本轮最后一条可见 assistant 正文替换为总结后的回复。"""
    for msg in reversed(messages):
        if msg.get("role") != "assistant":
            continue
        if msg.get("tool_calls"):
            continue
        if msg.get("content"):
            msg["content"] = text
            return
    for msg in reversed(messages):
        if msg.get("role") == "assistant" and msg.get("content"):
            msg["content"] = text
            return
