"""Zotero history tool truncation for LLM input."""

from __future__ import annotations

from firefly.agent.loop import (
    ZOTERO_HISTORY_TOOL_MAX_CHARS,
    _sanitize_history_for_llm,
)


def test_sanitize_history_truncates_tool_results() -> None:
    big = "x" * (ZOTERO_HISTORY_TOOL_MAX_CHARS + 500)
    history = [{"role": "tool", "name": "exec", "content": big}]
    out = _sanitize_history_for_llm(history, channel="zotero")
    assert len(str(out[0]["content"])) < len(big)
    assert "truncated" in str(out[0]["content"])


def test_sanitize_history_skips_non_zotero() -> None:
    big = "y" * 10_000
    history = [{"role": "tool", "content": big}]
    out = _sanitize_history_for_llm(history, channel="cli")
    assert out[0]["content"] == big
