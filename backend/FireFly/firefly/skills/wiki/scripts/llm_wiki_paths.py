"""Resolve ``llm-wiki`` root and load ``AGENTS.md`` for markdown-driven wiki tooling."""

from __future__ import annotations

from pathlib import Path

from loguru import logger


def load_llm_wiki_agents_md(wiki_root: Path) -> str | None:
    """Return UTF-8 text of ``llm-wiki/AGENTS.md`` if present and readable."""
    path = wiki_root / "AGENTS.md"
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        logger.warning("llm wiki: cannot read {}", path)
        return None


def resolve_llm_wiki_root(workspace: Path) -> Path | None:
    """Locate ``llm-wiki`` with a ``wiki/`` dir next to or above the workspace."""
    ws = workspace.expanduser().resolve()
    visited: set[Path] = set()
    for base in [ws, *ws.parents]:
        if base in visited:
            continue
        visited.add(base)
        cand = base / "llm-wiki"
        if (cand / "wiki").is_dir():
            return cand.resolve()
    return None


def resolve_wiki_mirror_from_raw_pdf_path(pdf_path_str: str) -> tuple[Path, Path] | None:
    """Map ``…/llm-wiki/raw/pdf/<rel>.pdf`` to ``(llm-wiki root, …/wiki/<rel>.md)``.

    Uses the same ``raw/pdf/`` segment rule as RAG / markdown tooling; returns ``None`` if
    the path does not contain ``raw/pdf/``.
    """
    raw = str(pdf_path_str).strip().strip('"')
    norm = raw.replace("\\", "/")
    lowered = norm.lower()
    marker = "raw/pdf/"
    idx = lowered.find(marker)
    if idx < 0:
        return None
    head = norm[:idx].rstrip("/")
    if not head:
        return None
    tail = norm[idx + len(marker) :].lstrip("/")
    if not tail:
        return None
    tail_parts = [p for p in tail.split("/") if p]
    if not tail_parts:
        return None
    wiki_root = Path(head)
    wiki_page = wiki_root / "wiki" / Path(*tail_parts).with_suffix(".md")
    return wiki_root, wiki_page
