"""Zotero library read tools."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from firefly.agent.tools.base import Tool, tool_parameters
from firefly.agent.tools.schema import BooleanSchema, IntegerSchema, StringSchema, tool_parameters_schema
from firefly.skills.zotero.scripts.zotero_literature_reader import (
    ZoteroReaderError,
    format_item_for_agent,
    format_search_for_agent,
    read_item,
    search_items,
)


@tool_parameters(
    tool_parameters_schema(
        item_id=IntegerSchema(description="Numeric Zotero item ID"),
        item_key=StringSchema(description="8-character Zotero item key"),
        include_storage_text=BooleanSchema(
            description=(
                "Include full literature body. Resolution order: "
                "llm-wiki/raw/markdown, then llm-wiki/raw/pdf, then Zotero storage"
            ),
        ),
        required=[],
    )
)
class ZoteroReadItemTool(Tool):
    """Read one Zotero item: metadata, notes, PDF annotations, optional attachment text."""

    name = "zotero_read_item"
    description = (
        "Read a Zotero library item by item_id or item_key. "
        "Returns title, authors, fields, tags, notes, PDF highlights/comments, "
        "and optional full text. Full text resolution order: "
        "llm-wiki/raw/markdown (.md) first, then raw/pdf, then Zotero storage. "
        "For long-form reading prefer read_file on the markdown path when present."
    )

    def __init__(self, data_dir: str | None = None, workspace: Path | None = None):
        self.data_dir = data_dir
        self.workspace = workspace

    @property
    def read_only(self) -> bool:
        return True

    async def execute(
        self,
        item_id: int | None = None,
        item_key: str | None = None,
        include_storage_text: bool | None = None,
        **kwargs: Any,
    ) -> str:
        if item_id is None and not item_key:
            return "Error: provide item_id or item_key"
        try:
            payload = read_item(
                item_id=item_id,
                item_key=item_key,
                include_storage_text=bool(include_storage_text),
                data_dir=self.data_dir,
                workspace=self.workspace,
            )
        except ZoteroReaderError as exc:
            return f"Error: {exc}"
        return format_item_for_agent(payload)


@tool_parameters(
    tool_parameters_schema(
        query=StringSchema("Search query (title, author, tag, or field text)"),
        limit=IntegerSchema(5, description="Max results (1-20)", minimum=1, maximum=20),
        required=["query"],
    )
)
class ZoteroSearchTool(Tool):
    """Search the local Zotero library for bibliographic items."""

    name = "zotero_search"
    description = (
        "Search the local Zotero library by title, author, tag, or other field text. "
        "Returns candidate items with item_id and key for follow-up zotero_read_item calls."
    )

    def __init__(self, data_dir: str | None = None, workspace: Path | None = None):
        self.data_dir = data_dir
        self.workspace = workspace

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, query: str, limit: int | None = None, **kwargs: Any) -> str:
        try:
            items = search_items(query, limit=limit or 5, data_dir=self.data_dir)
        except ZoteroReaderError as exc:
            return f"Error: {exc}"
        return format_search_for_agent(items, query)
