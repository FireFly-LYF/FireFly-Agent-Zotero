"""Extract heading structure from llm-wiki markdown (line numbers for agent tools)."""

from __future__ import annotations

import re
from typing import Any

from firefly.skills.markdown.scripts.markdown_to_rag import (
    ATX_HEADING_RE,
    SETEXT_HEADING_RE,
    _logical_heading_level,
    _numeric_heading_segments,
    _path_from_stack,
)

def extract_markdown_headings(text: str) -> list[dict[str, Any]]:
    """
    Return ATX/setext headings with 1-indexed line numbers and hierarchical section_path.

    One pass over the file — use instead of repeated grep when locating sections.
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    headings: list[dict[str, Any]] = []
    stack: list[tuple[int, str]] = []
    total = len(lines)
    i = 0

    while i < total:
        line = lines[i]
        line_no = i + 1
        atx = ATX_HEADING_RE.match(line)
        if atx:
            title = atx.group(2).strip()
            atx_level = len(atx.group(1))
            logical = _logical_heading_level(title, atx_level)
            while stack and stack[-1][0] >= logical:
                stack.pop()
            stack.append((logical, title))
            headings.append(
                {
                    "level": logical,
                    "line": line_no,
                    "text": title,
                    "section_path": _path_from_stack(stack),
                }
            )
            i += 1
            continue

        if line.strip() and i + 1 < total:
            nxt = lines[i + 1]
            if SETEXT_HEADING_RE.match(nxt):
                title = line.strip()
                underline = nxt.strip()
                atx_level = 1 if underline.startswith("=") else 2
                logical = _logical_heading_level(title, atx_level)
                while stack and stack[-1][0] >= logical:
                    stack.pop()
                stack.append((logical, title))
                headings.append(
                    {
                        "level": logical,
                        "line": line_no,
                        "text": title,
                        "section_path": _path_from_stack(stack),
                    }
                )
                i += 2
                continue

        i += 1

    return headings


def heading_line_for_section_path(
    section_path: str,
    headings: list[dict[str, Any]],
) -> int | None:
    """Map a RAG section_path to the best matching heading line (1-indexed)."""
    if not section_path or not headings:
        return None
    target = section_path.split(">")[-1].strip()
    if not target:
        return None

    for h in headings:
        if str(h.get("text", "")).strip() == target:
            return int(h["line"])

    target_norm = _normalize_heading_key(target)
    for h in headings:
        if _normalize_heading_key(str(h.get("text", ""))) == target_norm:
            return int(h["line"])

    for h in headings:
        sp = str(h.get("section_path", "")).strip()
        if sp == section_path.strip():
            return int(h["line"])

    target_num = _numeric_heading_segments(target)
    if target_num:
        for h in headings:
            segs = _numeric_heading_segments(str(h.get("text", "")))
            if segs == target_num:
                return int(h["line"])

    return None


def _normalize_heading_key(title: str) -> str:
    s = re.sub(r"\*+", "", title)
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s
