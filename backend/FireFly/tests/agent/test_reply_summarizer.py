"""Tests for Zotero reply summarizer."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from firefly.agent.summarizer import (
    build_summarizer_user_prompt,
    collect_zotero_turn_user_query,
    finalize_zotero_stream_reply,
    replace_last_assistant_text,
    summarize_zotero_reply,
)


def test_build_summarizer_user_prompt_includes_query_and_tools():
    prompt = build_summarizer_user_prompt(
        user_query="总结实验步骤",
        tool_events=[{"name": "read_file", "status": "ok", "detail": "paper.md"}],
        agent_draft="内部草稿",
    )
    assert "总结实验步骤" in prompt
    assert "read_file" in prompt
    assert "内部草稿" in prompt


def test_replace_last_assistant_text_skips_tool_call_rows():
    messages = [
        {"role": "assistant", "content": "calling tool", "tool_calls": [{"id": "1"}]},
        {"role": "tool", "content": "result"},
        {"role": "assistant", "content": "raw draft"},
    ]
    replace_last_assistant_text(messages, "用户可见总结")
    assert messages[-1]["content"] == "用户可见总结"
    assert messages[0]["content"] == "calling tool"


def test_summarize_zotero_reply_streams_and_returns_text():
    provider = MagicMock()
    chunks: list[str] = []

    async def on_stream(delta: str) -> None:
        chunks.append(delta)

    async def _fake_stream(**kwargs):
        on_delta = kwargs["on_content_delta"]
        await on_delta("最终")
        await on_delta("回复")
        return MagicMock(content="最终回复")

    provider.chat_stream_with_retry = AsyncMock(side_effect=_fake_stream)

    result = asyncio.run(
        summarize_zotero_reply(
            provider,
            "test-model",
            user_query="问题",
            tool_events=[],
            agent_draft="草稿",
            on_stream=on_stream,
        )
    )
    assert result == "最终回复"
    assert chunks == ["最终", "回复"]


def test_collect_zotero_turn_user_query_includes_injected_followups():
    query = collect_zotero_turn_user_query(
        "[zotero_literature_read_hint] hint\n第一个问题",
        [
            {"role": "user", "content": "第一个问题"},
            {"role": "assistant", "content": "ok", "tool_calls": [{"id": "1"}]},
            {"role": "user", "content": "继续补充"},
        ],
        had_injections=True,
    )
    assert "第一个问题" in query
    assert "继续补充" in query


def test_finalize_zotero_stream_reply_skips_summarizer_without_tools():
    provider = MagicMock()
    provider.chat_stream_with_retry = AsyncMock()
    provider.chat_with_retry = AsyncMock()
    streamed: list[str] = []

    async def on_answer(delta: str) -> None:
        streamed.append(delta)

    async def on_end(*, resuming: bool = False) -> None:
        assert resuming is False

    result = asyncio.run(
        finalize_zotero_stream_reply(
            provider,
            "test-model",
            user_query="问题",
            tool_events=[],
            agent_draft="直接回复",
            on_answer_stream=on_answer,
            on_stream_end=on_end,
        )
    )
    assert result == "直接回复"
    assert streamed == ["直接回复"]
    provider.chat_stream_with_retry.assert_not_called()
    provider.chat_with_retry.assert_not_called()


def test_finalize_zotero_stream_reply_summarizes_once_after_tool_task():
    provider = MagicMock()
    provider.chat_with_retry = AsyncMock(return_value=MagicMock(content="任务总结"))

    result = asyncio.run(
        finalize_zotero_stream_reply(
            provider,
            "test-model",
            user_query="写 docx",
            tool_events=[{"name": "read_file", "status": "ok", "detail": "paper.md"}],
            agent_draft="内部草稿",
        )
    )
    assert result == "任务总结"
    provider.chat_with_retry.assert_called_once()
