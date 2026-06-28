"""Tests for ContextBuilder Zotero LLM post-processing."""

from __future__ import annotations

import json

from firefly.agent.context import ContextBuilder, _summarize_tool_content_for_llm
from firefly.agent.loop import _sanitize_history_for_llm


def test_summarize_read_file_keeps_preview_not_full():
    full = "\n".join(f"{i}| line {i}" for i in range(1, 200))
    summary = _summarize_tool_content_for_llm("read_file", full)
    assert "full text in session" in summary
    assert len(summary) < len(full)
    assert "1|" in summary


def test_summarize_create_from_markdown_json():
    payload = json.dumps({"path": "D:/a.docx", "paragraph_count": 91, "heading_count": 30})
    summary = _summarize_tool_content_for_llm("mcp_docx-mcp_create_from_markdown", payload)
    assert "paragraph_count" in summary
    assert len(summary) < 500


def test_prepare_does_not_mutate_original_messages():
    full = "z" * 5000
    original = [{"role": "tool", "name": "read_file", "content": full}]
    summarized = ContextBuilder.prepare_messages_for_llm(original, channel="zotero")
    assert original[0]["content"] == full
    assert len(str(summarized[0]["content"])) < len(full)


def test_sanitize_history_uses_summaries():
    full = "q" * 8000
    history = [{"role": "tool", "name": "grep", "content": full}]
    out = _sanitize_history_for_llm(history, channel="zotero")
    assert history[0]["content"] == full
    assert len(str(out[0]["content"])) < len(full)
