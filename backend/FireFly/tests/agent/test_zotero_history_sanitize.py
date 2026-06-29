"""Zotero history tool summarization: defer until after turn ends."""

from __future__ import annotations

from firefly.agent.context import ContextBuilder
from firefly.agent.loop import _sanitize_history_for_llm
from firefly.session.manager import Session


def test_sanitize_history_keeps_full_tool_results_during_turn() -> None:
    big = "x" * 10_000
    history = [{"role": "tool", "name": "read_file", "content": big}]
    out = _sanitize_history_for_llm(history, channel="zotero")
    assert history[0]["content"] == big
    assert out[0]["content"] == big


def test_compress_session_tool_results_after_turn() -> None:
    big = "y" * 10_000
    session = Session(key="zotero:chat-1")
    session.messages.append({"role": "tool", "name": "read_file", "content": big})
    n = ContextBuilder.compress_session_tool_results(session, 0)
    assert n == 1
    assert session.messages[0].get("tool_result_summarized") is True
    assert len(str(session.messages[0]["content"])) < len(big)
    assert "session" in str(session.messages[0]["content"]).lower()
    # Idempotent: already summarized
    assert ContextBuilder.compress_session_tool_results(session, 0) == 0
