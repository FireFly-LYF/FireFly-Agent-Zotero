"""将发给 LLM 的请求快照脱敏后再用于终端 / 回调展示（不影响真实 API 请求体）。"""

from __future__ import annotations

import copy
from typing import Any


def redact_llm_request_for_display(payload: dict[str, Any]) -> dict[str, Any]:
    """去掉冗长 tools schema、tool 消息正文、assistant.tool_calls 的 arguments 等。"""
    out = copy.deepcopy(payload)

    if "tools" in out:
        tools = out.get("tools")
        if isinstance(tools, dict) and tools.get("_redacted_tool_definitions"):
            pass
        elif tools:
            names: list[str] = []
            if isinstance(tools, list):
                for t in tools:
                    if not isinstance(t, dict):
                        continue
                    fn = t.get("function")
                    if isinstance(fn, dict):
                        n = fn.get("name")
                        if isinstance(n, str) and n.strip():
                            names.append(n.strip())
            out["tools"] = {
                "_redacted_tool_definitions": True,
                "tool_count": len(names) if names else (len(tools) if isinstance(tools, list) else 0),
                "tool_names": names,
            }
        else:
            out["tools"] = {
                "_redacted_tool_definitions": True,
                "tool_count": 0,
                "tool_names": [],
            }

    if out.get("functions"):
        fn = out["functions"]
        out["functions"] = {
            "_redacted_legacy_functions": True,
            "count": len(fn) if isinstance(fn, list) else 1,
        }

    choice = out.get("tool_choice")
    if isinstance(choice, dict):
        out["tool_choice"] = "<redacted>"
    elif isinstance(choice, str) and len(choice) > 120:
        out["tool_choice"] = choice[:120] + "..."

    msgs = out.get("messages")
    if not isinstance(msgs, list):
        return out
    new_list: list[Any] = []
    for raw in msgs:
        if not isinstance(raw, dict):
            new_list.append(raw)
            continue
        m = copy.deepcopy(raw)
        role = str(m.get("role") or "")
        if role == "tool":
            c = m.get("content")
            if isinstance(c, str):
                m["content"] = f"[tool output omitted, {len(c)} chars]"
            elif isinstance(c, list):
                m["content"] = f"[tool output omitted, {len(c)} content blocks]"
            else:
                m["content"] = "[tool output omitted]"
        elif role == "assistant":
            tcs = m.get("tool_calls")
            if isinstance(tcs, list) and tcs:
                slim: list[dict[str, Any]] = []
                for tc in tcs:
                    if not isinstance(tc, dict):
                        continue
                    fn = tc.get("function")
                    name = None
                    if isinstance(fn, dict):
                        n = fn.get("name")
                        name = n if isinstance(n, str) else None
                    slim.append(
                        {
                            "id": tc.get("id"),
                            "type": tc.get("type", "function"),
                            "function": {"name": name, "arguments": "<omitted>"},
                        }
                    )
                m["tool_calls"] = slim
        new_list.append(m)
    out["messages"] = new_list
    return out
