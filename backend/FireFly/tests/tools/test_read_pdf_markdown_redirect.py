"""read_file redirects llm-wiki PDF paths to markdown mirrors."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from firefly.agent.tools.filesystem import ReadFileTool


@pytest.fixture
def wiki_tree(tmp_path: Path) -> tuple[Path, Path, Path]:
    pdf = tmp_path / "llm-wiki" / "raw" / "pdf" / "topic" / "paper.pdf"
    md = tmp_path / "llm-wiki" / "raw" / "markdown" / "topic" / "paper.md"
    pdf.parent.mkdir(parents=True)
    md.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF-1.4\n")
    md.write_text("# Title\n\nReferences section here.\n", encoding="utf-8")
    return tmp_path, pdf, md


def test_read_file_prefers_markdown_mirror(wiki_tree) -> None:
    workspace, pdf, md = wiki_tree
    tool = ReadFileTool(workspace=workspace, allowed_dir=workspace)

    async def _run() -> str:
        return await tool.execute(path=str(pdf))

    out = asyncio.run(_run())
    assert "Redirected from PDF to llm-wiki markdown mirror" in str(out)
    assert "References section here." in str(out)
    assert str(md) in str(out)
