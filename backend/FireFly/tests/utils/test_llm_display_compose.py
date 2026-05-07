"""Tests for partitioned LLM request display."""

from firefly.utils.llm_display_compose import build_llm_display_payload


def test_legacy_full_redact_when_llm_input_print_absent() -> None:
    prefs = {"show_llm_input": True}
    payload = {
        "phase": "primary",
        "iteration": 0,
        "model": "m",
        "messages": [{"role": "tool", "content": "secret-tool-body"}],
        "tools": [{"type": "function", "function": {"name": "fn", "description": "x"}}],
    }
    out = build_llm_display_payload(prefs, payload)
    assert out["messages"][0]["content"].startswith("[tool output omitted")
    assert out["tools"].get("_redacted_tool_definitions") is True


def test_partitioned_tools_includes_raw_definitions() -> None:
    prefs = {
        "show_llm_input": True,
        "llm_input_print": {
            "tools": True,
            "rag": False,
            "skills": False,
            "user": False,
            "system": False,
        },
    }
    tools = [{"type": "function", "function": {"name": "read_file", "description": "read"}}]
    payload = {
        "phase": "primary",
        "iteration": 1,
        "model": "m",
        "messages": [{"role": "tool", "content": "full body"}],
        "tools": tools,
    }
    out = build_llm_display_payload(prefs, payload)
    assert out.get("llm_input_mode") == "partitioned"
    sec = out["llm_input_sections"]["tools"]
    assert sec["definitions"] == tools
    assert sec["tool_messages"][0]["content"] == "full body"


def test_partitioned_rag_and_user_strip() -> None:
    rag = "[RAG Context]\nchunk\n[/RAG Context]"
    prefs = {
        "show_llm_input": True,
        "llm_input_print": {
            "tools": False,
            "rag": True,
            "skills": False,
            "user": True,
            "system": False,
        },
    }
    payload = {
        "phase": "primary",
        "iteration": 0,
        "model": "m",
        "messages": [{"role": "user", "content": f"{rag}\n\nhello"}],
        "tools": None,
    }
    out = build_llm_display_payload(prefs, payload)
    assert rag in out["llm_input_sections"]["rag"]
    u = out["llm_input_sections"]["user"][0]["content"]
    assert "[RAG Context]" not in u
    assert "hello" in u


def test_partitioned_skills_slice() -> None:
    prefs = {
        "show_llm_input": True,
        "llm_input_print": {
            "tools": False,
            "rag": False,
            "skills": True,
            "user": False,
            "system": False,
        },
    }
    sys = "intro\n\n---\n\n# Active Skills\n\nskill body\n\n---\n\n# Memory\n\nx"
    payload = {
        "phase": "primary",
        "iteration": 0,
        "model": "m",
        "messages": [{"role": "system", "content": sys}],
    }
    out = build_llm_display_payload(prefs, payload)
    assert "Active Skills" in out["llm_input_sections"]["skills"]
    assert "Memory" not in out["llm_input_sections"]["skills"]


def test_partitioned_all_false_shows_note() -> None:
    prefs = {
        "show_llm_input": True,
        "llm_input_print": {
            "tools": False,
            "rag": False,
            "skills": False,
            "user": False,
            "system": False,
        },
    }
    payload = {"phase": "p", "iteration": 0, "model": "m", "messages": []}
    out = build_llm_display_payload(prefs, payload)
    assert "llm_input_sections_note" in out
