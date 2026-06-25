"""RAG agent tools and path helpers."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from firefly.agent.tools.rag import RagIndexTool, RagSearchTool
from firefly.skills.markdown.scripts.rag_paths import (
    llm_wiki_markdown_mirror_for_pdf,
    resolve_markdown_path_from_pdf,
    resolve_rag_jsonl_from_markdown,
    resolve_rag_path_from_wiki_pdf,
)


def test_resolve_markdown_path_from_pdf() -> None:
    pdf = Path("D:/wiki/llm-wiki/raw/pdf/topic/paper.pdf")
    md = resolve_markdown_path_from_pdf(pdf)
    assert md.as_posix().endswith("raw/markdown/topic/paper.md")


def test_resolve_rag_jsonl_from_markdown() -> None:
    md = Path("D:/wiki/llm-wiki/raw/markdown/topic/paper.md")
    rag = resolve_rag_jsonl_from_markdown(md)
    assert rag.as_posix().endswith("raw/rag/topic/paper.jsonl")


def test_llm_wiki_markdown_mirror_for_pdf(tmp_path: Path) -> None:
    pdf = tmp_path / "llm-wiki" / "raw" / "pdf" / "a" / "demo.pdf"
    md = tmp_path / "llm-wiki" / "raw" / "markdown" / "a" / "demo.md"
    md.parent.mkdir(parents=True)
    md.write_text("# body", encoding="utf-8")
    got = llm_wiki_markdown_mirror_for_pdf(pdf)
    assert got == md.resolve()


def test_resolve_rag_path_from_wiki_pdf(tmp_path: Path) -> None:
    rag_file = tmp_path / "llm-wiki" / "raw" / "rag" / "a" / "demo.jsonl"
    rag_file.parent.mkdir(parents=True)
    rag_file.write_text("{}\n", encoding="utf-8")
    pdf = tmp_path / "llm-wiki" / "raw" / "pdf" / "a" / "demo.pdf"
    got = resolve_rag_path_from_wiki_pdf(str(pdf))
    assert got == rag_file.resolve()


def test_rag_search_tool(tmp_path: Path) -> None:
    recs = [
        {"chunk_index": 0, "section_path": "D > 4.1", "text": "alpha unique_token"},
        {"chunk_index": 1, "section_path": "D > 4.2", "text": "beta other"},
    ]
    rag = tmp_path / "rag.jsonl"
    rag.write_text("\n".join(json.dumps(r) for r in recs), encoding="utf-8")

    tool = RagSearchTool()
    out = asyncio.run(tool.execute(query="unique_token", rag_path=str(rag)))
    assert "alpha unique_token" in out
    assert "section=[D > 4.1]" in out


def test_rag_index_tool_builds_jsonl(tmp_path: Path) -> None:
    md = tmp_path / "llm-wiki" / "raw" / "markdown" / "demo.md"
    md.parent.mkdir(parents=True)
    md.write_text("# Title\n\nBody paragraph.\n", encoding="utf-8")

    tool = RagIndexTool()
    out = asyncio.run(tool.execute(markdown_path=str(md)))
    rag = resolve_rag_jsonl_from_markdown(md)
    assert rag.is_file()
    assert "chunk_count" in out
