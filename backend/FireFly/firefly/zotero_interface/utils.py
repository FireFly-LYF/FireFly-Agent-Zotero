"""Shared helpers for Zotero bridge / UI (no heavy deps)."""

from __future__ import annotations

import re

_RAG_BLOCK = re.compile(r"\[RAG Context\][\s\S]*?\[/RAG Context\]\s*", re.MULTILINE)
_TEXT_CTX = re.compile(r"\[Text Context\][\s\S]*?\[/Text Context\]\s*", re.MULTILINE)
_WIKI_PDF_LINE = re.compile(r"^\[zotero_current_wiki_pdf_path=[^\]]+\]\s*\n?", re.MULTILINE)
_WIKI_MD_LINE = re.compile(r"^\[zotero_current_wiki_markdown_path=[^\]]+\]\s*\n?", re.MULTILINE)
_ITEM_HEAD = re.compile(
    r"^\[zotero_current_item_id=\d+\]\s*\n(?:当前 Zotero 条目[^\n]+\n+)?",
    re.MULTILINE,
)
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
    s = _RAG_REMINDER.sub("", s)
    s = _QUERY_REMINDER.sub("", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()
