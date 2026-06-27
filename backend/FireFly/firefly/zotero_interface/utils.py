"""Shared helpers for Zotero bridge / UI (no heavy deps)."""

from __future__ import annotations

import re
from typing import Any

_RAG_BLOCK = re.compile(r"\[RAG Context\][\s\S]*?\[/RAG Context\]\s*", re.MULTILINE)
_TEXT_CTX = re.compile(r"\[Text Context\][\s\S]*?\[/Text Context\]\s*", re.MULTILINE)
_WIKI_PDF_LINE = re.compile(r"^\[zotero_current_wiki_pdf_path=[^\]]+\]\s*\n?", re.MULTILINE)
_WIKI_MD_LINE = re.compile(r"^\[zotero_current_wiki_markdown_path=[^\]]+\]\s*\n?", re.MULTILINE)
_ITEM_HEAD = re.compile(
    r"^\[zotero_current_item_id=\d+\]\s*\n(?:当前 Zotero 条目[^\n]+\n+)?",
    re.MULTILINE,
)
_LITERATURE_READ_HINT = re.compile(r"^\[zotero_literature_read_hint\][^\n]*\n?", re.MULTILINE)
_MARKDOWN_MIRROR_MISSING = re.compile(r"^\[zotero_markdown_mirror_missing\][^\n]*\n?", re.MULTILINE)
_WIKI_PAGE_CTX = re.compile(r"\[Wiki Page Context\][\s\S]*?\[/Wiki Page Context\]\s*", re.MULTILINE)
_RAG_REMINDER = re.compile(r"\n----\n【RAG 再确认】[^\n]*(?:\n|$)")
_QUERY_REMINDER = re.compile(r"\n----\n【请直接回答此问（优先于旧对话）】[\s\S]*\Z")


def strip_zotero_user_content_for_session_storage(text: str) -> str:
    """Remove RAG blocks, path markers, and bridge trailers; keep the user's actual prompt."""
    if not isinstance(text, str):
        return text
    s = text
    s = _RAG_BLOCK.sub("", s)
    s = _TEXT_CTX.sub("", s)
    s = _WIKI_PDF_LINE.sub("", s)
    s = _WIKI_MD_LINE.sub("", s)
    s = _ITEM_HEAD.sub("", s)
    s = _LITERATURE_READ_HINT.sub("", s)
    s = _MARKDOWN_MIRROR_MISSING.sub("", s)
    s = _WIKI_PAGE_CTX.sub("", s)
    s = _RAG_REMINDER.sub("", s)
    s = _QUERY_REMINDER.sub("", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


# 带 tool_calls 的 assistant 若正文过长，落盘时压成一行摘要（保留 tool_calls 供 LLM 续跑）。
ZOTERO_TOOL_CALL_ASSISTANT_MAX_CONTENT = 800
ZOTERO_TOOL_CALL_ASSISTANT_MAX_REASONING = 2_000


def tool_call_names(tool_calls: list[Any] | None) -> list[str]:
    names: list[str] = []
    if not isinstance(tool_calls, list):
        return names
    for tc in tool_calls:
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function")
        if isinstance(fn, dict):
            name = str(fn.get("name") or "").strip()
        else:
            name = ""
        names.append(name or "tool")
    return names


def compact_tool_call_assistant_for_storage(entry: dict[str, Any]) -> dict[str, Any]:
    """将 tool-call 中间步的超长正文/reasoning 压成摘要，避免污染会话与后续 token。"""
    if entry.get("role") != "assistant" or not entry.get("tool_calls"):
        return entry
    out = dict(entry)
    content = out.get("content")
    if isinstance(content, str) and len(content.strip()) > ZOTERO_TOOL_CALL_ASSISTANT_MAX_CONTENT:
        names = tool_call_names(out.get("tool_calls"))
        label = ", ".join(names) if names else "tools"
        out["content"] = f"[Tool step: {label}]"
    reasoning = out.get("reasoning_content")
    if isinstance(reasoning, str) and len(reasoning) > ZOTERO_TOOL_CALL_ASSISTANT_MAX_REASONING:
        out["reasoning_content"] = (
            reasoning[: ZOTERO_TOOL_CALL_ASSISTANT_MAX_REASONING - 3].rstrip() + "..."
        )
    return out


def is_zotero_user_visible_message(msg: dict[str, Any]) -> bool:
    """插件 /history 仅展示 user 与最终 assistant（无 tool_calls）。"""
    role = str(msg.get("role", ""))
    if role == "user":
        return True
    if role == "assistant" and not msg.get("tool_calls"):
        return True
    return False
