"""Tests for Zotero session display / persistence helpers."""

from __future__ import annotations

from firefly.agent.loop import AgentLoop
from firefly.session.manager import Session
from firefly.zotero_interface.utils import (
    compact_tool_call_assistant_for_storage,
    is_zotero_user_visible_message,
    strip_zotero_user_content_for_session_storage,
)


def test_strip_zotero_user_content_removes_literature_read_hint() -> None:
    raw = (
        "[zotero_literature_read_hint] Full-text: grep the markdown path above "
        "to locate sections.\n"
        "总结本文的实验步骤"
    )
    assert strip_zotero_user_content_for_session_storage(raw) == "总结本文的实验步骤"


def test_strip_zotero_user_content_removes_bridge_markers() -> None:
    raw = (
        "[zotero_current_wiki_pdf_path=/tmp/a.pdf]\n"
        "[zotero_current_wiki_markdown_path=/tmp/a.md]\n"
        "[zotero_literature_read_hint] Do NOT read_file the PDF.\n"
        "[zotero_markdown_mirror_missing] Expected markdown at: /tmp/a.md\n"
        "用户问题"
    )
    assert strip_zotero_user_content_for_session_storage(raw) == "用户问题"


def test_is_zotero_user_visible_message_skips_tool_call_assistant() -> None:
    assert is_zotero_user_visible_message({"role": "user", "content": "hi"})
    assert is_zotero_user_visible_message({"role": "assistant", "content": "done"})
    assert not is_zotero_user_visible_message(
        {"role": "assistant", "content": "draft", "tool_calls": [{"id": "1"}]}
    )
    assert not is_zotero_user_visible_message({"role": "tool", "content": "x"})


def test_compact_tool_call_assistant_replaces_long_draft() -> None:
    long_body = "x" * 5000
    entry = {
        "role": "assistant",
        "content": long_body,
        "tool_calls": [{"id": "c1", "function": {"name": "read_file"}}],
        "reasoning_content": "y" * 5000,
    }
    out = compact_tool_call_assistant_for_storage(entry)
    assert out["content"] == "[Tool step: read_file]"
    assert len(str(out["reasoning_content"])) < 5000
    assert str(out["reasoning_content"]).endswith("...")


def test_compact_tool_call_assistant_leaves_final_message() -> None:
    entry = {"role": "assistant", "content": "short final"}
    assert compact_tool_call_assistant_for_storage(entry)["content"] == "short final"


def test_save_turn_compacts_zotero_tool_call_assistant() -> None:
    loop = AgentLoop.__new__(AgentLoop)
    from firefly.config.schema import AgentDefaults

    loop.max_tool_result_chars = AgentDefaults().max_tool_result_chars
    session = Session(key="zotero:chat-1")
    loop._save_turn(
        session,
        [{
            "role": "assistant",
            "content": "z" * 3000,
            "tool_calls": [{"id": "c1", "function": {"name": "mcp_docx-mcp_create_from_markdown"}}],
        }],
        skip=0,
        literature_title="Paper Title",
    )
    assert session.messages[0]["content"] == "[Tool step: mcp_docx-mcp_create_from_markdown]"
