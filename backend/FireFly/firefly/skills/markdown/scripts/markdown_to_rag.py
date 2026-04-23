#!/usr/bin/env python3
"""
Batch chunk Markdown files for RAG with mirrored directory layout.

Default mapping:
- Markdown root: backend/llm-wiki/raw/markdown
- RAG root: backend/llm-wiki/raw/rag

Output keeps the same relative directory structure. Each markdown file generates
one JSONL file with chunk records.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MARKDOWN_ROOT = Path("backend/llm-wiki/raw/markdown")
DEFAULT_RAG_ROOT = Path("backend/llm-wiki/raw/rag")
DEFAULT_MAX_CHARS = 1600

IMAGE_LINE_RE = re.compile(r"^\s*!\[[^\]]*\]\(([^)]+)\)\s*$")
PICTURE_TEXT_START_RE = re.compile(r"^\s*\*\*----- Start of picture text -----\*\*<br>\s*$")
PICTURE_TEXT_END_RE = re.compile(r"^\s*\*\*----- End of picture text -----\*\*<br>\s*$")
ATX_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.*)$")
SETEXT_HEADING_RE = re.compile(r"^\s*(=+|-+)\s*$")


@dataclass
class Block:
    text: str
    has_image_ref: bool
    image_refs: list[str]
    heading: str | None = None


def _iter_markdown_files(markdown_root: Path) -> list[Path]:
    return sorted(path for path in markdown_root.rglob("*.md") if path.is_file())


def _target_rag_path(md_path: Path, markdown_root: Path, rag_root: Path) -> Path:
    relative = md_path.relative_to(markdown_root)
    return (rag_root / relative).with_suffix(".jsonl")


def _split_blocks(text: str) -> list[Block]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[Block] = []
    i = 0
    total = len(lines)

    def read_paragraph(start: int) -> tuple[Block, int]:
        j = start
        while j < total and lines[j].strip() == "":
            j += 1
        begin = j
        while j < total and lines[j].strip() != "":
            j += 1
        paragraph_lines = lines[begin:j]
        heading = _extract_heading(paragraph_lines, lines, begin, j)
        text_block = "\n".join(paragraph_lines).strip("\n")
        return Block(text=text_block, has_image_ref=False, image_refs=[], heading=heading), j

    while i < total:
        if lines[i].strip() == "":
            i += 1
            continue

        image_match = IMAGE_LINE_RE.match(lines[i])
        if image_match:
            refs = [image_match.group(1)]
            block_lines = [lines[i]]
            i += 1

            # 保留图片引用后的空行，尽量维持视觉语义邻接关系。
            while i < total and lines[i].strip() == "":
                block_lines.append(lines[i])
                i += 1

            # 将图片 OCR 文本块视为原子单元，防止切片时被切碎。
            if i < total and PICTURE_TEXT_START_RE.match(lines[i]):
                block_lines.append(lines[i])
                i += 1
                while i < total:
                    block_lines.append(lines[i])
                    if PICTURE_TEXT_END_RE.match(lines[i]):
                        i += 1
                        break
                    i += 1
                while i < total and lines[i].strip() == "":
                    block_lines.append(lines[i])
                    i += 1

            blocks.append(
                Block(
                    text="\n".join(block_lines).strip("\n"),
                    has_image_ref=True,
                    image_refs=refs,
                    heading=None,
                )
            )
            continue

        paragraph, i = read_paragraph(i)
        if paragraph.text:
            blocks.append(paragraph)

    return blocks


def _extract_heading(paragraph_lines: list[str], all_lines: list[str], begin: int, end: int) -> str | None:
    if not paragraph_lines:
        return None
    first = paragraph_lines[0]
    atx = ATX_HEADING_RE.match(first)
    if atx:
        return atx.group(2).strip()

    # setext heading occupies two lines and underline appears on next line.
    if end < len(all_lines):
        second = all_lines[end]
        if SETEXT_HEADING_RE.match(second):
            return first.strip()
    return None


def _make_chunks(blocks: list[Block], max_chars: int) -> list[dict[str, object]]:
    blocks = _normalize_blocks(blocks, max_chars=max_chars)
    chunks: list[dict[str, object]] = []
    cur_blocks: list[Block] = []
    cur_len = 0
    section_stack: list[str] = []

    def flush() -> None:
        nonlocal cur_blocks, cur_len
        if not cur_blocks:
            return
        text = "\n\n".join(block.text for block in cur_blocks if block.text).strip()
        if not text:
            cur_blocks = []
            cur_len = 0
            return
        image_refs: list[str] = []
        has_image_ref = False
        for block in cur_blocks:
            if block.has_image_ref:
                has_image_ref = True
                image_refs.extend(block.image_refs)
        chunks.append(
            {
                "chunk_index": len(chunks),
                "text": text,
                "char_count": len(text),
                "has_image_ref": has_image_ref,
                "image_refs": image_refs,
                "section_path": " > ".join(section_stack) if section_stack else "",
            }
        )
        cur_blocks = []
        cur_len = 0

    for block in blocks:
        if block.heading:
            # 用最近出现的标题维护轻量级 section 路径，方便检索过滤。
            if section_stack and section_stack[-1] == block.heading:
                pass
            else:
                section_stack.append(block.heading)
                section_stack = section_stack[-4:]

        block_len = len(block.text)
        if cur_blocks and cur_len + 2 + block_len > max_chars:
            flush()
        if not cur_blocks:
            cur_blocks = [block]
            cur_len = block_len
        else:
            cur_blocks.append(block)
            cur_len += 2 + block_len

    flush()
    return chunks


def _normalize_blocks(blocks: list[Block], max_chars: int) -> list[Block]:
    normalized: list[Block] = []
    for block in blocks:
        if block.has_image_ref and len(IMAGE_LINE_RE.findall(block.text)) > 1:
            sub_blocks = _split_malformed_image_block(block)
        else:
            sub_blocks = [block]
        for sub in sub_blocks:
            if len(sub.text) > max_chars:
                normalized.extend(_split_large_block_by_lines(sub, max_chars=max_chars))
            else:
                normalized.append(sub)
    return normalized


def _split_malformed_image_block(block: Block) -> list[Block]:
    lines = block.text.split("\n")
    parts: list[list[str]] = []
    current: list[str] = []
    image_refs: list[str] = []
    current_refs: list[str] = []

    for line in lines:
        matched = IMAGE_LINE_RE.match(line)
        if matched:
            image_refs.append(matched.group(1))
            if current:
                parts.append(current)
                current = []
                current_refs = []
            current_refs.append(matched.group(1))
        current.append(line)
        if current_refs:
            # keep tracking refs belonging to current part
            pass

    if current:
        parts.append(current)

    if len(parts) <= 1:
        return [block]

    out: list[Block] = []
    for part_lines in parts:
        text = "\n".join(part_lines).strip("\n")
        refs = [m.group(1) for m in (IMAGE_LINE_RE.match(x) for x in part_lines) if m]
        out.append(
            Block(
                text=text,
                has_image_ref=bool(refs),
                image_refs=refs,
                heading=block.heading,
            )
        )
    return out


def _split_large_block_by_lines(block: Block, max_chars: int) -> list[Block]:
    lines = block.text.split("\n")
    out: list[Block] = []
    cur: list[str] = []
    cur_len = 0
    for line in lines:
        line_len = len(line) if not cur else len(line) + 1
        if cur and cur_len + line_len > max_chars:
            text = "\n".join(cur).strip("\n")
            refs = [m.group(1) for m in (IMAGE_LINE_RE.match(x) for x in cur) if m]
            out.append(
                Block(
                    text=text,
                    has_image_ref=bool(refs),
                    image_refs=refs,
                    heading=block.heading,
                )
            )
            cur = [line]
            cur_len = len(line)
        else:
            cur.append(line)
            cur_len += line_len
    if cur:
        text = "\n".join(cur).strip("\n")
        refs = [m.group(1) for m in (IMAGE_LINE_RE.match(x) for x in cur) if m]
        out.append(
            Block(
                text=text,
                has_image_ref=bool(refs),
                image_refs=refs,
                heading=block.heading,
            )
        )
    return out if out else [block]


def _convert_one(md_path: Path, markdown_root: Path, rag_root: Path, max_chars: int) -> dict[str, object]:
    raw = md_path.read_text(encoding="utf-8", errors="ignore")
    blocks = _split_blocks(raw)
    chunks = _make_chunks(blocks, max_chars=max_chars)
    out_path = _target_rag_path(md_path, markdown_root, rag_root)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    records = []
    rel_md = str(md_path.relative_to(markdown_root))
    for chunk in chunks:
        records.append(
            {
                "source_markdown": rel_md,
                **chunk,
            }
        )

    with out_path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    return {
        "source_markdown": rel_md,
        "target_jsonl": str(out_path.relative_to(rag_root)),
        "chunk_count": len(records),
    }


def convert_one(markdown_path: Path, rag_path: Path, max_chars: int) -> dict[str, object]:
    if not markdown_path.exists():
        raise FileNotFoundError(f"Markdown file does not exist: {markdown_path}")
    if not markdown_path.is_file():
        raise ValueError(f"Markdown path is not a file: {markdown_path}")
    if markdown_path.suffix.lower() != ".md":
        raise ValueError(f"Input file must be a .md: {markdown_path}")

    raw = markdown_path.read_text(encoding="utf-8", errors="ignore")
    blocks = _split_blocks(raw)
    chunks = _make_chunks(blocks, max_chars=max_chars)
    rag_path.parent.mkdir(parents=True, exist_ok=True)

    records: list[dict[str, object]] = []
    for chunk in chunks:
        records.append({"source_markdown": str(markdown_path), **chunk})

    with rag_path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    return {
        "source_markdown": str(markdown_path),
        "target_jsonl": str(rag_path),
        "chunk_count": len(records),
    }


def convert_all(markdown_root: Path, rag_root: Path, max_chars: int) -> dict[str, object]:
    if not markdown_root.exists():
        raise FileNotFoundError(f"Markdown root does not exist: {markdown_root}")
    if not markdown_root.is_dir():
        raise NotADirectoryError(f"Markdown root is not a directory: {markdown_root}")

    converted: list[dict[str, object]] = []
    failed: list[dict[str, str]] = []
    for md_path in _iter_markdown_files(markdown_root):
        try:
            converted.append(_convert_one(md_path, markdown_root, rag_root, max_chars=max_chars))
        except Exception as exc:  # noqa: BLE001
            failed.append(
                {
                    "source_markdown": str(md_path.relative_to(markdown_root)),
                    "error": str(exc),
                }
            )

    return {
        "markdown_root": str(markdown_root),
        "rag_root": str(rag_root),
        "max_chars": max_chars,
        "converted": converted,
        "failed": failed,
        "count": {
            "converted_files": len(converted),
            "failed_files": len(failed),
            "total_files": len(converted) + len(failed),
        },
    }


def _load_chunk_preview(rag_jsonl_path: Path, limit: int) -> list[dict[str, object]]:
    if not rag_jsonl_path.exists() or not rag_jsonl_path.is_file():
        return []
    out: list[dict[str, object]] = []
    lines = rag_jsonl_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    for line in lines:
        if len(out) >= limit:
            break
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        out.append(
            {
                "chunk_index": rec.get("chunk_index"),
                "char_count": rec.get("char_count"),
                "has_image_ref": rec.get("has_image_ref"),
                "section_path": rec.get("section_path"),
                "text_preview": str(rec.get("text", "") or "")[:300],
            }
        )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Chunk llm-wiki markdown files into mirror-structured JSONL files for RAG."
    )
    parser.add_argument(
        "--markdown-root",
        default=str(DEFAULT_MARKDOWN_ROOT),
        help="Root directory for source markdown files.",
    )
    parser.add_argument(
        "--markdown",
        default="",
        help="Convert one markdown file instead of batch mode.",
    )
    parser.add_argument(
        "--rag-root",
        default=str(DEFAULT_RAG_ROOT),
        help="Root directory for output RAG chunk files.",
    )
    parser.add_argument(
        "--rag",
        default="",
        help="Output JSONL path for single-file mode (--markdown).",
    )
    parser.add_argument(
        "--max-chars",
        type=int,
        default=DEFAULT_MAX_CHARS,
        help="Soft limit of characters per chunk. Atomic image blocks may exceed it.",
    )
    parser.add_argument(
        "--show-chunks",
        action="store_true",
        help="Show chunk previews in JSON output.",
    )
    parser.add_argument(
        "--show-limit",
        type=int,
        default=5,
        help="Max chunk previews to show when --show-chunks is enabled.",
    )
    args = parser.parse_args()

    markdown_root = Path(args.markdown_root).expanduser().resolve()
    rag_root = Path(args.rag_root).expanduser().resolve()
    max_chars = max(200, int(args.max_chars))
    show_limit = max(1, int(args.show_limit))

    single_markdown = str(args.markdown or "").strip()
    single_rag = str(args.rag or "").strip()
    if single_markdown:
        markdown_path = Path(single_markdown).expanduser().resolve()
        if single_rag:
            rag_path = Path(single_rag).expanduser().resolve()
        else:
            rag_path = markdown_path.with_suffix(".jsonl")
        result = {
            "mode": "single",
            "max_chars": max_chars,
            "result": convert_one(markdown_path=markdown_path, rag_path=rag_path, max_chars=max_chars),
        }
        if args.show_chunks:
            result["chunk_preview"] = _load_chunk_preview(rag_path, limit=show_limit)
    else:
        result = convert_all(markdown_root=markdown_root, rag_root=rag_root, max_chars=max_chars)
        result["mode"] = "batch"
        if args.show_chunks:
            with_preview: list[dict[str, object]] = []
            for item in result.get("converted", []):
                target = str(item.get("target_jsonl", "")).strip()
                if not target:
                    continue
                rag_jsonl_path = (rag_root / target).resolve()
                with_preview.append(
                    {
                        "target_jsonl": target,
                        "chunk_preview": _load_chunk_preview(rag_jsonl_path, limit=show_limit),
                    }
                )
            result["chunk_preview"] = with_preview
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if single_markdown:
        return 0
    return 0 if not result["failed"] else 1


if __name__ == "__main__":
    sys.exit(main())
