"""Generate or refresh a mirrored ``wiki/**/*.md`` page from ``raw/markdown`` source."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger

from firefly.skills.wiki.scripts.llm_wiki_paths import load_llm_wiki_agents_md, resolve_llm_wiki_root


def _strip_outer_markdown_fences(text: str) -> str:
    t = text.strip()
    if not t.startswith("```"):
        return t
    lines = t.split("\n")
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def wiki_output_path_for_raw_markdown(wiki_root: Path, markdown_path: Path) -> Path:
    """``raw/markdown/a/b.md`` → ``wiki/a/b.md`` under the same ``llm-wiki`` root (pure join)."""
    wr = wiki_root.expanduser()
    md = markdown_path.expanduser()
    raw_root = wr / "raw" / "markdown"
    rel = md.relative_to(raw_root)
    return wr / "wiki" / rel


def build_wiki_ingest_system_prompt(wiki_root: Path) -> str:
    """System prompt: AGENTS.md + instructions to emit one mirrored wiki page."""
    agents = load_llm_wiki_agents_md(wiki_root)
    tail = (
        "\n\n---\n\n## 本任务的输出约束\n\n"
        "- 只输出**一个** wiki 镜像页的正文 Markdown（将写入 `wiki/<与 raw/markdown 相同的相对路径>.md`），"
        "不要外层 Markdown 代码围栏。\n"
        "- 章节结构须覆盖 `wiki/_templates/paper.md` 中的分区与要点；不得编造原文未出现的具体数字或实验结论。\n"
        "- 「概述与导航」等节**禁止**写入指向其它 `wiki/**/*.md` 的 Markdown 链接，除非你确信该路径已在磁盘存在；"
        "宁可只写主题说明或留空，也不要杜撰交叉引用。\n"
        "- 「来源」一节必须写明本任务给出的 **`raw/markdown/...`** 相对路径（相对于 `llm-wiki` 根目录）。\n"
    )
    if agents:
        return (
            "你是文献 Wiki 维护助手。根据下面给出的 **`raw/markdown`** 文献正文，"
            "撰写或更新与之**目录镜像、同主文件名**的 `wiki/**/*.md` 单篇页面。\n\n"
            "【权威规范】你必须完整遵守以下 **`llm-wiki/AGENTS.md`** 全文。\n\n"
            "---\n\n"
            + agents
            + tail
        )
    return (
        "你是文献 Wiki 维护助手。根据用户提供的 raw/markdown 文献正文，"
        "输出一个镜像到 wiki/ 的 Markdown 页面，结构与 `wiki/_templates/paper.md` 一致；"
        "不得编造；「来源」须写 raw/markdown 相对路径。不要代码围栏。"
        + tail
    )


MAX_SOURCE_CHARS = 120_000


async def run_wiki_ingest_from_markdown(loop: Any, markdown_path: Path) -> dict[str, Any]:
    """Read ``markdown_path``, call LLM, write the mirrored page under ``wiki/``."""
    wiki_root = resolve_llm_wiki_root(loop.workspace)
    if wiki_root is None:
        return {"ok": False, "detail": "no_llm_wiki", "markdown_path": str(markdown_path), "wiki_path": None}

    try:
        md = markdown_path.expanduser().resolve()
    except OSError:
        md = markdown_path.expanduser()

    wr = wiki_root.resolve()
    raw_root = (wr / "raw" / "markdown").resolve()
    try:
        md.relative_to(raw_root)
    except ValueError:
        return {
            "ok": False,
            "detail": "markdown_outside_raw_tree",
            "markdown_path": str(md),
            "wiki_path": None,
        }

    if not md.is_file():
        return {"ok": False, "detail": "markdown_not_found", "markdown_path": str(md), "wiki_path": None}

    try:
        body_text = md.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        logger.warning("wiki ingest: cannot read {}", md)
        return {"ok": False, "detail": "markdown_read_error", "error": str(e), "markdown_path": str(md), "wiki_path": None}

    truncated = False
    if len(body_text) > MAX_SOURCE_CHARS:
        body_text = (
            body_text[:MAX_SOURCE_CHARS]
            + "\n\n…（正文过长已截断；后续内容未纳入本轮整理。）\n"
        )
        truncated = True

    template_path = wr / "wiki" / "_templates" / "paper.md"
    template_excerpt = ""
    if template_path.is_file():
        try:
            template_excerpt = template_path.read_text(encoding="utf-8", errors="replace")[:8000]
        except OSError:
            pass

    rel_from_wiki_root = md.relative_to(wr).as_posix()
    out_path = wiki_output_path_for_raw_markdown(wr, md)
    try:
        out_resolved = out_path.resolve()
    except OSError:
        out_resolved = out_path

    system_prompt = build_wiki_ingest_system_prompt(wr)
    user_parts = [
        f"事实来源（须在「来源」节引用）：`{rel_from_wiki_root}`\n",
        f"输出将写入（路径须与之一致）：`{out_path.relative_to(wr).as_posix()}`\n",
    ]
    if template_excerpt:
        user_parts.append("\n## 分区模板（`wiki/_templates/paper.md` 摘录）\n\n" + template_excerpt + "\n")
    user_parts.append("\n---\n\n## 文献 Markdown 正文\n\n" + body_text)
    user_prompt = "".join(user_parts)

    try:
        resp = await loop.provider.chat_with_retry(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            tools=None,
            model=loop.model,
            temperature=0.2,
            max_tokens=16384,
            reasoning_effort=None,
        )
    except Exception:
        logger.exception("wiki markdown ingest: LLM call failed ({})", md)
        return {"ok": False, "detail": "llm_error", "markdown_path": str(md), "wiki_path": str(out_path)}

    content = (resp.content or "").strip()
    if not content:
        return {"ok": False, "detail": "empty_llm_output", "markdown_path": str(md), "wiki_path": str(out_path)}

    content = _strip_outer_markdown_fences(content)
    out_resolved.parent.mkdir(parents=True, exist_ok=True)
    out_resolved.write_text(content.strip() + "\n", encoding="utf-8")

    return {
        "ok": True,
        "detail": "written",
        "markdown_path": str(md),
        "wiki_path": str(out_resolved),
        "source_truncated": truncated,
    }
