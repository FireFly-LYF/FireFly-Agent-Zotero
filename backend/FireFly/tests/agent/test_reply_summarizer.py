"""Tests for Zotero reply summarizer."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from firefly.agent.reply_summarizer import (
    build_summarizer_user_prompt,
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
