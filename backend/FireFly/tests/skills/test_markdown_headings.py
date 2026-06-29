"""Tests for markdown heading extraction and RAG line hints."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from firefly.agent.tools.rag import GetMarkdownHeadingsTool
from firefly.skills.markdown.scripts.markdown_headings import (
    extract_markdown_headings,
    heading_line_for_section_path,
)
from firefly.skills.markdown.scripts.rag_paths import format_rag_chunks_for_agent


def test_extract_markdown_headings_numeric_sections() -> None:
    text = """# Title

Intro body.

## **4** 方法

Method text.

## **4.2** 仿真实验

Experiment details.
"""
    headings = extract_markdown_headings(text)
    assert len(headings) >= 3
    by_text = {h["text"]: h for h in headings}
    assert by_text["**4** 方法"]["line"] == 5
    assert by_text["**4.2** 仿真实验"]["line"] == 9
    assert "方法" in by_text["**4.2** 仿真实验"]["section_path"]


def test_heading_line_for_section_path() -> None:
    headings = extract_markdown_headings("## **4.2** 仿真实验\n\nBody.\n")
    line = heading_line_for_section_path("方法 > **4.2** 仿真实验", headings)
    assert line == 1


def test_get_markdown_headings_tool(tmp_path: Path) -> None:
    md = tmp_path / "paper.md"
    md.write_text("## **3** 方法\n\nText.\n", encoding="utf-8")
    tool = GetMarkdownHeadingsTool()
    out = asyncio.run(tool.execute(path=str(md)))
    data = json.loads(out)
    assert data[0]["line"] == 1
    assert "方法" in data[0]["text"]


def test_format_rag_chunks_includes_start_line(tmp_path: Path) -> None:
    md_root = tmp_path / "llm-wiki" / "raw" / "markdown"
    rag_root = tmp_path / "llm-wiki" / "raw" / "rag"
    md = md_root / "demo.md"
    md.parent.mkdir(parents=True)
    md.write_text(
        "## **4.2** 仿真实验\n\nUnique experiment token here.\n",
        encoding="utf-8",
    )
    rag = rag_root / "demo.jsonl"
    rag.parent.mkdir(parents=True)
    rec = {
        "chunk_index": 0,
        "section_path": "**4.2** 仿真实验",
        "text": "Unique experiment token here.",
    }
    rag.write_text(json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")

    out = format_rag_chunks_for_agent("experiment", rag, [rec])
    assert "start_line=1" in out
    assert "Unique experiment token" in out
