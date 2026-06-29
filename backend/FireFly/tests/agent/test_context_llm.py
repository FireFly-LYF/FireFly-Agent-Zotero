"""Tests for ContextBuilder Zotero LLM post-processing."""

from __future__ import annotations

import json

from firefly.agent.context import ContextBuilder, _summarize_tool_content_for_llm
from firefly.agent.loop import _sanitize_history_for_llm
from firefly.session.manager import Session


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


def test_summarize_get_body_text_keeps_preview_not_full():
    body = "\n".join(f"para {i}" for i in range(1, 100))
    payload = json.dumps({"body": body, "footnotes": "fn1"})
    summary = _summarize_tool_content_for_llm("mcp_docx-mcp_get_body_text", payload)
    assert "full text in session" in summary
    assert "para 1" in summary
    assert len(summary) < len(payload)


def test_summarize_rag_search_keeps_chunk_headers():
    payload = "\n".join([
        "RAG search results (source: /x.jsonl)",
        "query: 4.2 方法",
        "Retrieved 2 chunk(s), showing top 2.",
        "--- chunk 1 (index=0 section=[4 > 4.2]) start_line=120 ---",
        "method body alpha " + "x" * 500,
        "--- chunk 2 (index=1 section=[4 > 4.3]) start_line=200 ---",
        "other " + "y" * 500,
    ])
    summary = _summarize_tool_content_for_llm("rag_search", payload)
    assert "--- chunk 1" in summary
    assert "start_line=120" in summary
    assert "avoid repeat rag_search" in summary
    assert len(summary) < len(payload)


def test_prepare_messages_does_not_compress_tool_results():
    full = "z" * 5000
    original = [{"role": "tool", "name": "read_file", "content": full}]
    out = ContextBuilder.prepare_messages_for_llm(original, channel="zotero")
    assert original[0]["content"] == full
    assert out[0]["content"] == full


def test_sanitize_history_does_not_compress():
    full = "q" * 8000
    history = [{"role": "tool", "name": "grep", "content": full}]
    out = _sanitize_history_for_llm(history, channel="zotero")
    assert history[0]["content"] == full
    assert out[0]["content"] == full


def test_compress_session_tool_results_mutates_session_only():
    full = "z" * 5000
    session = Session(key="zotero:t")
    session.messages.append({"role": "tool", "name": "read_file", "content": full})
    ContextBuilder.compress_session_tool_results(session, 0)
    assert len(str(session.messages[0]["content"])) < len(full)
