"""Zotero history tool summarization for LLM input."""

from __future__ import annotations

from firefly.agent.loop import _sanitize_history_for_llm


def test_sanitize_history_summarizes_tool_results() -> None:
    big = "x" * 10_000
    history = [{"role": "tool", "name": "read_file", "content": big}]
    out = _sanitize_history_for_llm(history, channel="zotero")
    assert history[0]["content"] == big
    assert len(str(out[0]["content"])) < len(big)
    assert "session" in str(out[0]["content"]).lower()
