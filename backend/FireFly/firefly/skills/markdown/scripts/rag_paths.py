"""RAG 路径解析与检索结果格式化（供 agent 工具与 Zotero bridge 共用）。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from loguru import logger

from firefly.skills.markdown.scripts.rag_utils import retrieve_rag_chunks_with_chapter_expansion

_MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")


def is_llm_wiki_pdf_path(path: Path | str) -> bool:
    return "raw/pdf/" in str(path).replace("\\", "/").lower()


def llm_wiki_markdown_mirror_for_pdf(pdf_path: Path | str) -> Path | None:
    """若存在 ``raw/pdf`` 对应的 ``raw/markdown`` 镜像，返回该 ``.md`` 路径。"""
    if not is_llm_wiki_pdf_path(pdf_path):
        return None
    md = resolve_markdown_path_from_pdf(Path(pdf_path))
    try:
        resolved = md.expanduser().resolve()
    except OSError:
        resolved = md.expanduser()
    return resolved if resolved.is_file() else None


def resolve_markdown_path_from_pdf(pdf_path: Path) -> Path:
    """Map ``.../raw/pdf/<rel>.pdf`` -> ``.../raw/markdown/<rel>.md``."""
    p = pdf_path.expanduser()
    try:
        p = p.resolve()
    except OSError:
        pass
    parts = list(p.parts)
    low = [x.lower() for x in parts]
    for i in range(len(low) - 1):
        if low[i] == "raw" and low[i + 1] == "pdf":
            root = Path(parts[0]).joinpath(*parts[1:i]) if i > 0 else Path(parts[0])
            tail_parts = parts[i + 2 :]
            if not tail_parts:
                return root / "raw" / "markdown" / "document.md"
            rel = Path(*tail_parts)
            return (root / "raw" / "markdown" / rel).with_suffix(".md")
    return p.with_suffix(".md")


def resolve_rag_path_from_wiki_pdf(pdf_path: str) -> Path | None:
    """根据 llm-wiki ``raw/pdf/`` 镜像路径解析同结构的 ``raw/rag/*.jsonl``。"""
    if not pdf_path:
        return None
    raw = str(pdf_path).strip().strip('"')
    norm = raw.replace("\\", "/")
    lowered = norm.lower()
    marker = "raw/pdf/"
    idx = lowered.find(marker)
    if idx < 0:
        return None
    head = norm[:idx].rstrip("/")
    tail = norm[idx + len(marker) :].lstrip("/")
    if not tail:
        return None
    tail_parts = [p for p in tail.split("/") if p]
    primary = Path(head)
    for part in ("raw", "rag", *tail_parts):
        primary = primary / part
    primary = primary.with_suffix(".jsonl")
    try:
        if primary.is_file():
            return primary.resolve()
    except OSError:
        if primary.is_file():
            return primary

    stem = Path(tail_parts[-1]).stem
    rag_root = Path(head) / "raw" / "rag"
    try:
        if rag_root.is_dir():
            matches = list(rag_root.rglob(f"{stem}.jsonl"))
            if len(matches) == 1:
                logger.info(
                    "RAG jsonl resolved via basename fallback: {} -> {}",
                    pdf_path,
                    matches[0],
                )
                return matches[0].resolve()
            if len(matches) > 1:
                logger.warning(
                    "Multiple RAG jsonl for stem {!r} under {}, skipping ambiguous fallback",
                    stem,
                    rag_root,
                )
    except OSError as exc:
        logger.warning("RAG basename fallback failed for {!r}: {}", pdf_path, exc)

    try:
        exists = primary.exists()
    except OSError:
        exists = False
    if not exists:
        logger.warning("RAG jsonl not found for wiki pdf path (primary {!r})", primary)
    return primary if exists else None


def resolve_rag_jsonl_from_markdown(markdown_path: Path) -> Path:
    """``raw/markdown`` 下 ``.md`` 对应 ``raw/rag`` 下同相对路径 ``.jsonl``。"""
    text = str(markdown_path)
    marker = "raw\\markdown\\"
    marker_alt = "raw/markdown/"
    lowered = text.lower()
    idx = lowered.find(marker)
    if idx < 0:
        idx = lowered.find(marker_alt)
    if idx >= 0:
        head = text[:idx]
        tail = text[idx + len(marker) :] if lowered.find(marker) >= 0 else text[idx + len(marker_alt) :]
        rag_root = Path(f"{head}raw/rag")
        return (rag_root / tail).with_suffix(".jsonl")
    return markdown_path.with_suffix(".jsonl")


def resolve_markdown_root_from_rag_path(rag_path: Path) -> Path | None:
    text = str(rag_path)
    lowered = text.lower().replace("\\", "/")
    marker = "raw/rag/"
    idx = lowered.find(marker)
    if idx < 0:
        return None
    head = text[:idx]
    return Path(f"{head}raw/markdown")


def rewrite_chunk_image_refs_for_multimodal(
    chunk_text: str, rec: dict[str, Any], rag_path: Path
) -> str:
    """把 chunk 中 markdown 图片引用改为绝对本地路径。"""
    if not chunk_text or "![" not in chunk_text:
        return chunk_text
    source_markdown = str(rec.get("source_markdown", "") or "").strip()
    if not source_markdown:
        return chunk_text
    markdown_root = resolve_markdown_root_from_rag_path(rag_path)
    if not markdown_root:
        return chunk_text
    md_path = (markdown_root / source_markdown).resolve()
    md_parent = md_path.parent

    def _replace(match: re.Match[str]) -> str:
        raw_ref = str(match.group(1) or "").strip().strip("<>")
        if " " in raw_ref and not Path(raw_ref).exists():
            raw_ref = raw_ref.split(" ", 1)[0].strip()
        if not raw_ref:
            return match.group(0)
        ref_path = Path(raw_ref).expanduser()
        candidate = ref_path if ref_path.is_absolute() else (md_parent / ref_path).resolve()
        if not candidate.is_file():
            return match.group(0)
        return match.group(0).replace(match.group(1), str(candidate))

    return _MARKDOWN_IMAGE_RE.sub(_replace, chunk_text)


def ensure_markdown_rag_index(markdown_path: Path, *, max_chars: int | None = None) -> dict[str, Any]:
    """将 Markdown 切片写入对应 RAG jsonl；返回索引元数据。"""
    from firefly.config.cli_prefs import get_markdown_rag_prefs
    from firefly.config.loader import get_config_path
    from firefly.skills.markdown.scripts.markdown_to_rag import convert_one

    md = markdown_path.expanduser().resolve()
    if not md.is_file():
        raise FileNotFoundError(f"markdown not found for rag: {md}")
    rag_path = resolve_rag_jsonl_from_markdown(md)
    try:
        rag_resolved = rag_path.expanduser().resolve()
    except OSError:
        rag_resolved = rag_path.expanduser()
    had_prior_rag_index = rag_resolved.is_file()
    if max_chars is None:
        max_chars = max(200, int(get_markdown_rag_prefs(get_config_path())["max_chars"]))
    conv = convert_one(md, rag_resolved, max_chars)
    return {
        "rag_path": str(rag_resolved),
        "rag_chunk_count": int(conv.get("chunk_count") or 0),
        "rag_reindexed": True,
        "had_prior_rag_index": had_prior_rag_index,
    }


def resolve_rag_path(
    *,
    wiki_pdf_path: str | None = None,
    rag_path: str | None = None,
    markdown_path: str | None = None,
) -> Path | None:
    """按优先级解析 RAG jsonl：显式 rag_path > wiki_pdf_path > markdown_path。"""
    if rag_path and str(rag_path).strip():
        p = Path(str(rag_path).strip()).expanduser()
        try:
            p = p.resolve()
        except OSError:
            pass
        return p if p.is_file() else None
    if wiki_pdf_path and str(wiki_pdf_path).strip():
        return resolve_rag_path_from_wiki_pdf(str(wiki_pdf_path).strip())
    if markdown_path and str(markdown_path).strip():
        md = Path(str(markdown_path).strip()).expanduser()
        try:
            md = md.resolve()
        except OSError:
            pass
        rag = resolve_rag_jsonl_from_markdown(md)
        try:
            rag = rag.expanduser().resolve()
        except OSError:
            rag = rag.expanduser()
        return rag if rag.is_file() else None
    return None


def search_rag_chunks(
    query: str,
    rag_jsonl_path: Path,
    *,
    top_k: int = 12,
) -> list[dict[str, Any]]:
    return retrieve_rag_chunks_with_chapter_expansion(query, rag_jsonl_path, top_k=top_k)


def format_rag_chunks_for_agent(
    query: str,
    rag_path: Path,
    chunks: list[dict[str, Any]],
    *,
    max_chunk_chars: int = 900,
) -> str:
    if not chunks:
        return (
            f"No RAG chunks matched query={query!r} in {rag_path}. "
            "Try rag_index if the jsonl is missing or stale, or broaden the query."
        )
    lines = [
        f"RAG search results (source: {rag_path})",
        f"query: {query}",
        (
            f"Retrieved {len(chunks)} chunk(s). Chapter titles are prioritized; "
            "matching a subsection may expand the parent chapter. Chunks are ordered by relevance "
            "then document index."
        ),
        (
            "Use these passages as the primary factual basis. If the user names a section "
            "(e.g. 4.2), prefer chunks whose section=[…] matches. Formulas may contain OCR "
            "errors—normalize to LaTeX ($...$ / $$...$$) before answering. Do not cite chunk "
            "numbers in user-visible prose."
        ),
    ]
    for i, rec in enumerate(chunks, start=1):
        txt = str(rec.get("text", "") or "").strip()
        txt = rewrite_chunk_image_refs_for_multimodal(txt, rec, rag_path)
        if len(txt) > max_chunk_chars:
            txt = txt[:max_chunk_chars] + " ..."
        section_path = str(rec.get("section_path", "") or "").strip()
        section_info = f" section=[{section_path}]" if section_path else ""
        lines.append(f"--- chunk {i} (index={rec.get('chunk_index', i - 1)}{section_info}) ---")
        lines.append(txt)
    return "\n".join(lines)
