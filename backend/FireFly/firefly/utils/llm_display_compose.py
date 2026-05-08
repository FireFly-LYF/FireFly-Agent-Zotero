"""按 ``user.json`` 的 ``llm_input_print`` 将 LLM 请求拆成若干段用于终端展示（不发往 API）。"""

from __future__ import annotations

import copy
import json
import re
from typing import Any

from firefly.config.cli_prefs import LLM_INPUT_PRINT_KEYS
from firefly.utils.llm_display_redact import redact_llm_request_for_display

_RAG_BLOCK_RE = re.compile(r"\[RAG Context\][\s\S]*?\[/RAG Context\]", re.MULTILINE)


def _flatten_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
            elif isinstance(block, dict):
                parts.append(json.dumps(block, ensure_ascii=False)[:800])
            else:
                parts.append(str(block))
        return "\n".join(parts)
    return str(content)


def _extract_rag_from_messages(messages: list[Any]) -> str:
    chunks: list[str] = []
    for raw in messages:
        if not isinstance(raw, dict):
            continue
        text = _flatten_content(raw.get("content"))
        for m in _RAG_BLOCK_RE.finditer(text):
            chunks.append(m.group(0).strip())
    return "\n\n".join(chunks).strip()


def _strip_rag_blocks(text: str) -> str:
    return _RAG_BLOCK_RE.sub("", text).strip()


def _extract_skills_sections(system_text: str) -> str:
    segments: list[str] = []
    for block in system_text.split("\n\n---\n\n"):
        s = block.strip()
        if s.startswith("# Active Skills") or s.startswith("# Skills"):
            segments.append(block)
    return "\n\n---\n\n".join(segments).strip()


def build_partitioned_llm_display(
    payload: dict[str, Any],
    flags: dict[str, bool],
) -> dict[str, Any]:
    """*flags* 为各段是否包含；全为 False 时仍返回带提示的分段结构。"""
    base: dict[str, Any] = {
        "phase": payload.get("phase"),
        "iteration": payload.get("iteration"),
        "model": payload.get("model"),
        "llm_input_mode": "partitioned",
    }
    msgs = payload.get("messages")
    if not isinstance(msgs, list):
        msgs = []

    sections: dict[str, Any] = {}

    if flags.get("system"):
        sys_msgs = [copy.deepcopy(m) for m in msgs if isinstance(m, dict) and m.get("role") == "system"]
        if sys_msgs:
            sections["system"] = sys_msgs

    if flags.get("skills"):
        sys_text = "\n\n---\n\n".join(
            _flatten_content(m.get("content")) for m in msgs if isinstance(m, dict) and m.get("role") == "system"
        )
        skills_txt = _extract_skills_sections(sys_text)
        if skills_txt:
            sections["skills"] = skills_txt

    if flags.get("rag"):
        rag_txt = _extract_rag_from_messages(msgs)
        if rag_txt:
            sections["rag"] = rag_txt

    if flags.get("user"):
        user_out: list[dict[str, Any]] = []
        for raw in msgs:
            if not isinstance(raw, dict) or raw.get("role") != "user":
                continue
            m = copy.deepcopy(raw)
            c = m.get("content")
            if isinstance(c, str):
                m["content"] = _strip_rag_blocks(c)
            elif isinstance(c, list):
                new_blocks: list[Any] = []
                for block in c:
                    if isinstance(block, dict) and block.get("type") == "text":
                        t = _strip_rag_blocks(str(block.get("text", "")))
                        nb = dict(block)
                        nb["text"] = t
                        new_blocks.append(nb)
                    else:
                        new_blocks.append(block)
                m["content"] = new_blocks
            user_out.append(m)
        filtered = [m for m in user_out if _flatten_content(m.get("content")).strip()]
        if filtered:
            sections["user"] = filtered

    if flags.get("tools"):
        tool_msgs = [copy.deepcopy(m) for m in msgs if isinstance(m, dict) and m.get("role") == "tool"]
        call_msgs = [
            copy.deepcopy(m)
            for m in msgs
            if isinstance(m, dict)
            and m.get("role") == "assistant"
            and m.get("tool_calls")
        ]
        sections["tools"] = {
            "definitions": copy.deepcopy(payload.get("tools")),
            "tool_choice": copy.deepcopy(payload.get("tool_choice")),
            "functions": copy.deepcopy(payload.get("functions")),
            "tool_messages": tool_msgs,
            "assistant_tool_calls": call_msgs,
        }

    if not sections:
        base["llm_input_sections_note"] = (
            "llm_input_print 已启用但各子项均为 false，无输出；"
            "请将 tools / rag / skills / user / system 中至少一项设为 true。"
        )
    else:
        base["llm_input_sections"] = sections

    return base


def build_llm_display_payload(prefs: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """根据 ``user.json`` 偏好返回整段脱敏 JSON 或分段结构。

    未配置 ``llm_input_print`` 时：与原先一致，输出整份脱敏后的请求。
    配置了 ``llm_input_print`` 对象时：仅输出对应子项为 true 的段落（工具段为未脱敏的 definitions + 消息）。
    """
    lip = prefs.get("llm_input_print")
    if not isinstance(lip, dict):
        return redact_llm_request_for_display(payload)
    flags = {k: bool(lip.get(k)) for k in LLM_INPUT_PRINT_KEYS}
    return build_partitioned_llm_display(payload, flags)
