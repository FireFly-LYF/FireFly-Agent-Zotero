"""Local literature RAG tools (search + index)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from firefly.agent.tools.base import Tool, tool_parameters
from firefly.agent.tools.schema import BooleanSchema, IntegerSchema, StringSchema, tool_parameters_schema
from firefly.skills.markdown.scripts.rag_paths import (
    ensure_markdown_rag_index,
    format_rag_chunks_for_agent,
    resolve_markdown_path_from_pdf,
    resolve_rag_jsonl_from_markdown,
    resolve_rag_path,
    search_rag_chunks,
)


def _resolve_markdown_source(
    *,
    markdown_path: str | None,
    wiki_pdf_path: str | None,
) -> Path | None:
    if markdown_path and str(markdown_path).strip():
        p = Path(str(markdown_path).strip()).expanduser()
        try:
            p = p.resolve()
        except OSError:
            pass
        return p if p.is_file() else None
    if wiki_pdf_path and str(wiki_pdf_path).strip():
        md = resolve_markdown_path_from_pdf(Path(str(wiki_pdf_path).strip()))
        try:
            md = md.expanduser().resolve()
        except OSError:
            md = md.expanduser()
        return md if md.is_file() else None
    return None


@tool_parameters(
    tool_parameters_schema(
        query=StringSchema("Search query (user question or keywords; supports section numbers like 4.2)"),
        wiki_pdf_path=StringSchema(
            description=(
                "llm-wiki raw/pdf path for the open document "
                "(often from [zotero_current_wiki_pdf_path=…] in the user message)"
            ),
        ),
        rag_path=StringSchema(description="Explicit path to a .jsonl RAG index"),
        markdown_path=StringSchema(
            description=(
                "Preferred: llm-wiki raw/markdown path "
                "(often from [zotero_current_wiki_markdown_path=…]); resolves sibling raw/rag/*.jsonl"
            ),
        ),
        top_k=IntegerSchema(12, description="Max chunks to retrieve (1-30)", minimum=1, maximum=30),
        ensure_index=BooleanSchema(
            description=(
                "If true and RAG jsonl is missing but markdown exists, build the index before searching"
            ),
        ),
        required=["query"],
    )
)
class RagSearchTool(Tool):
    """Search local RAG chunks for a literature document."""

    name = "rag_search"
    description = (
        "Retrieve relevant passages from a local RAG index (.jsonl) for literature Q&A. "
        "**Use this first** for summarize / method-extraction / multi-section tasks — "
        "not grep. Prefer markdown_path (from [Zotero Runtime Context] or "
        "[zotero_current_wiki_markdown_path=…]) over wiki_pdf_path. "
        "Use topical queries per section (e.g. 'section 3 method', 'STMF formula'). "
        "If the index is missing, call rag_index or set ensure_index=true."
    )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(
        self,
        query: str,
        wiki_pdf_path: str | None = None,
        rag_path: str | None = None,
        markdown_path: str | None = None,
        top_k: int | None = None,
        ensure_index: bool | None = None,
        **kwargs: Any,
    ) -> str:
        q = str(query or "").strip()
        if not q:
            return "Error: query is required"

        if not any(str(v or "").strip() for v in (wiki_pdf_path, rag_path, markdown_path)):
            return (
                "Error: provide wiki_pdf_path, rag_path, or markdown_path. "
                "In Zotero, copy the path from [zotero_current_wiki_pdf_path=…] in the user message."
            )

        resolved = resolve_rag_path(
            wiki_pdf_path=wiki_pdf_path,
            rag_path=rag_path,
            markdown_path=markdown_path,
        )
        if resolved is None or not resolved.is_file():
            if ensure_index:
                md = _resolve_markdown_source(
                    markdown_path=markdown_path,
                    wiki_pdf_path=wiki_pdf_path,
                )
                if md is None:
                    return (
                        "Error: RAG index not found and no markdown available to build it. "
                        "Run PDF→markdown conversion or call rag_index with markdown_path."
                    )
                try:
                    meta = await asyncio.to_thread(ensure_markdown_rag_index, md)
                except Exception as exc:
                    return f"Error: failed to build RAG index: {exc}"
                resolved = Path(str(meta["rag_path"]))
            else:
                expected = None
                if markdown_path and str(markdown_path).strip():
                    expected = resolve_rag_jsonl_from_markdown(Path(str(markdown_path).strip()))
                elif wiki_pdf_path and str(wiki_pdf_path).strip():
                    md = resolve_markdown_path_from_pdf(Path(str(wiki_pdf_path).strip()))
                    expected = resolve_rag_jsonl_from_markdown(md)
                hint = f" Expected: {expected}" if expected else ""
                return (
                    f"Error: RAG index not found.{hint} "
                    "Call rag_index or rag_search with ensure_index=true after markdown exists."
                )

        k = max(1, min(30, int(top_k or 12)))
        chunks = await asyncio.to_thread(search_rag_chunks, q, resolved, top_k=k)
        return format_rag_chunks_for_agent(q, resolved, chunks)


@tool_parameters(
    tool_parameters_schema(
        markdown_path=StringSchema(description="Markdown file under llm-wiki/raw/markdown"),
        wiki_pdf_path=StringSchema(
            description="llm-wiki raw/pdf path; markdown mirror path is derived automatically",
        ),
        force=BooleanSchema(description="Rebuild even if the RAG jsonl already exists"),
        required=[],
    )
)
class RagIndexTool(Tool):
    """Build or refresh a Markdown → RAG (.jsonl) index."""

    name = "rag_index"
    description = (
        "Slice a markdown file into local RAG chunks (raw/markdown → raw/rag, mirrored layout). "
        "Use after PDF→markdown conversion or when rag_search reports a missing/stale index."
    )

    async def execute(
        self,
        markdown_path: str | None = None,
        wiki_pdf_path: str | None = None,
        force: bool | None = None,
        **kwargs: Any,
    ) -> str:
        md = _resolve_markdown_source(markdown_path=markdown_path, wiki_pdf_path=wiki_pdf_path)
        if md is None:
            return "Error: provide markdown_path or wiki_pdf_path pointing to an existing .md file"

        rag_out = resolve_rag_jsonl_from_markdown(md)
        try:
            rag_resolved = rag_out.expanduser().resolve()
        except OSError:
            rag_resolved = rag_out.expanduser()

        if rag_resolved.is_file() and not force:
            return (
                f"RAG index already exists: {rag_resolved}\n"
                "Use rag_search to query it, or call rag_index with force=true to rebuild."
            )

        try:
            meta = await asyncio.to_thread(ensure_markdown_rag_index, md)
        except Exception as exc:
            return f"Error: failed to index markdown: {exc}"

        return (
            f"RAG index built: {meta['rag_path']}\n"
            f"chunk_count: {meta['rag_chunk_count']}\n"
            f"had_prior_index: {meta['had_prior_rag_index']}"
        )
