"""文件系统工具：读、写、编辑、列目录。"""

import difflib
import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from firefly.agent.tools.base import Tool, tool_parameters
from firefly.agent.tools.schema import BooleanSchema, IntegerSchema, StringSchema, tool_parameters_schema
from firefly.agent.tools import file_state
from firefly.utils.helpers import build_image_content_blocks, detect_image_mime, is_tool_result_cache_path
from firefly.agent.temp import register_agent_temp_file, validate_scratch_write_path
from firefly.config.paths import get_media_dir
from firefly.skills.markdown.scripts.rag_paths import llm_wiki_markdown_mirror_for_pdf


def _resolve_path(
    path: str,
    workspace: Path | None = None,
    allowed_dir: Path | None = None,
    extra_allowed_dirs: list[Path] | None = None,
) -> Path:
    """将路径解析到工作区（若为相对路径）并执行目录访问限制。"""
    p = Path(path).expanduser()
    if not p.is_absolute() and workspace:
        p = workspace / p
    resolved = p.resolve()
    if allowed_dir:
        media_path = get_media_dir().resolve()
        all_dirs = [allowed_dir] + [media_path] + (extra_allowed_dirs or []) 
        if not any(_is_under(resolved, d) for d in all_dirs):
            raise PermissionError(f"Path {path} is outside allowed directory {allowed_dir}")
    return resolved


def _is_under(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory.resolve())
        return True
    except ValueError:
        return False


class _FsTool(Tool):
    """文件系统工具共享基类——统一初始化与路径解析。"""

    def __init__(
        self,
        workspace: Path | None = None,
        allowed_dir: Path | None = None,
        extra_allowed_dirs: list[Path] | None = None,
    ):
        self._workspace = workspace
        self._allowed_dir = allowed_dir
        self._extra_allowed_dirs = extra_allowed_dirs

    def _resolve(self, path: str) -> Path:
        return _resolve_path(path, self._workspace, self._allowed_dir, self._extra_allowed_dirs)

    def _guard_scratch_write(self, fp: Path) -> str | None:
        return validate_scratch_write_path(fp, self._workspace)

    def _note_temp_write(self, fp: Path) -> None:
        register_agent_temp_file(fp, self._workspace)


# ---------------------------------------------------------------------------
# 读取文件
# ---------------------------------------------------------------------------


_BLOCKED_DEVICE_PATHS = frozenset({
    "/dev/zero", "/dev/random", "/dev/urandom", "/dev/full",
    "/dev/stdin", "/dev/stdout", "/dev/stderr",
    "/dev/tty", "/dev/console",
    "/dev/fd/0", "/dev/fd/1", "/dev/fd/2",
})


def _is_blocked_device(path: str | Path) -> bool:
    """检查路径是否为被禁设备（可能卡住或产生无限输出）。"""
    import re
    raw = str(path)
    if raw in _BLOCKED_DEVICE_PATHS:
        return True
    if re.match(r"/proc/\d+/fd/[012]$", raw) or re.match(r"/proc/self/fd/[012]$", raw):
        return True
    return False


def _parse_page_range(pages: str, total: int) -> tuple[int, int]:
    """将如 '2-5' 的页码区间解析为从 0 开始且含端点的 (start, end)。"""
    parts = pages.strip().split("-")
    if len(parts) == 1:
        p = int(parts[0])
        return max(0, p - 1), min(p - 1, total - 1)
    start = int(parts[0])
    end = int(parts[1])
    return max(0, start - 1), min(end - 1, total - 1)


_LINE_NUM_PREFIX = re.compile(r"^\d+\|\s")


def _looks_numbered(lines: list[str]) -> bool:
    """检测文本是否已由 read_file 加过行号前缀。"""
    if not lines:
        return False
    sample = lines[: min(20, len(lines))]
    numbered = sum(1 for line in sample if _LINE_NUM_PREFIX.match(line))
    return numbered / len(sample) >= 0.5


def _strip_line_number_prefix(line: str) -> str:
    match = _LINE_NUM_PREFIX.match(line)
    return line[match.end():] if match else line


def _should_emit_plain_text(fp: Path, lines: list[str]) -> bool:
    """工具结果缓存或已带行号的文件不再二次加行号。"""
    return is_tool_result_cache_path(fp) or _looks_numbered(lines)


def _format_text_read(
    lines: list[str],
    *,
    offset: int,
    limit: int | None,
    default_limit: int,
    max_chars: int,
    plain: bool,
) -> tuple[str, int, int]:
    """按 offset/limit 截取文本并格式化输出。"""
    total = len(lines)
    if offset < 1:
        offset = 1
    if offset > total:
        return f"Error: offset {offset} is beyond end of file ({total} lines)", offset, total

    start = offset - 1
    end = min(start + (limit or default_limit), total)
    selected = lines[start:end]

    if plain:
        body = "\n".join(selected)
    else:
        body = "\n".join(f"{start + i + 1}| {line}" for i, line in enumerate(selected))

    if len(body) > max_chars:
        if plain:
            trimmed: list[str] = []
            chars = 0
            for line in selected:
                chars += len(line) + 1
                if chars > max_chars:
                    break
                trimmed.append(line)
            end = start + len(trimmed)
            body = "\n".join(trimmed)
        else:
            trimmed_lines: list[str] = []
            chars = 0
            for i, line in enumerate(selected):
                numbered = f"{start + i + 1}| {line}"
                chars += len(numbered) + 1
                if chars > max_chars:
                    break
                trimmed_lines.append(numbered)
            end = start + len(trimmed_lines)
            body = "\n".join(trimmed_lines)

    if end < total:
        suffix = (
            f"\n\n(Showing lines {offset}-{end} of {total}. Use offset={end + 1} to continue.)"
        )
    else:
        suffix = f"\n\n(End of file — {total} lines total)"
    if plain:
        suffix = f"\n\n(Raw text — line numbers omitted to avoid nested prefixes.){suffix}"
    return body + suffix, end, total


@tool_parameters(
    tool_parameters_schema(
        path=StringSchema("The file path to read"),
        offset=IntegerSchema(
            1,
            description="Line number to start reading from (1-indexed, default 1)",
            minimum=1,
        ),
        limit=IntegerSchema(
            2000,
            description="Maximum number of lines to read (default 2000)",
            minimum=1,
        ),
        pages=StringSchema("Page range for PDF files, e.g. '1-5' (default: all, max 20 pages)"),
        required=["path"],
    )
)
class ReadFileTool(_FsTool):
    """读取文件内容，支持按行分页。"""

    _MAX_CHARS = 128_000
    _DEFAULT_LIMIT = 2000
    _MAX_PDF_PAGES = 20

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return (
            "Read a file (text or image). Text output format: LINE_NUM|CONTENT. "
            "Cached tool-result files (.firefly/tool-results/) return raw text without "
            "line numbers to prevent nested prefixes. "
            "Images return visual content for analysis. "
            "For llm-wiki literature, prefer the mirrored "
            "backend/llm-wiki/raw/markdown/*.md (or [zotero_current_wiki_markdown_path=…]); "
            "do not read raw/pdf/*.pdf when the .md mirror exists. "
            "After rag_search locates a section, use read_file with offset/limit to expand "
            "formulas and steps — do NOT scan from offset=1 or rely on chapter-number guesses. "
            "Do not use read_file as a substitute for rag_search on summary tasks."
            "Use offset and limit for large files; use pages='1-5' only when no markdown mirror. "
            "Cannot read non-image binary files. "
            "Reads exceeding ~128K chars are truncated."
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, path: str | None = None, offset: int = 1, limit: int | None = None, pages: str | None = None, **kwargs: Any) -> Any:
        try:
            if not path:
                return "Error reading file: Unknown path"

            # 设备路径黑名单
            if _is_blocked_device(path):
                return f"Error: Reading {path} is blocked (device path that could hang or produce infinite output)."

            fp = self._resolve(path)
            if _is_blocked_device(fp):
                return f"Error: Reading {fp} is blocked (device path that could hang or produce infinite output)."
            if not fp.exists():
                return f"Error: File not found: {path}"
            if not fp.is_file():
                return f"Error: Not a file: {path}"

            pdf_redirect_note = ""
            if fp.suffix.lower() == ".pdf":
                md_mirror = llm_wiki_markdown_mirror_for_pdf(fp)
                if md_mirror is not None:
                    pdf_redirect_note = (
                        f"(Redirected from PDF to llm-wiki markdown mirror: {md_mirror})\n"
                    )
                    fp = md_mirror
                else:
                    from firefly.skills.markdown.scripts.rag_paths import resolve_markdown_path_from_pdf

                    expected_md = resolve_markdown_path_from_pdf(fp)
                    pdf_result = self._read_pdf(fp, pages)
                    if expected_md:
                        hint = (
                            f"(Note: no markdown mirror at {expected_md}. "
                            "Convert PDF→markdown first, then read_file the .md path. "
                            "PDF parsing is slower and was used as fallback.)\n"
                        )
                        return hint + pdf_result
                    return pdf_result

            plain_cache = is_tool_result_cache_path(fp)
            dedup_variant = "tool-result" if plain_cache else None
            if file_state.is_unchanged(fp, offset=offset, limit=limit, variant=dedup_variant):
                unchanged = f"[File unchanged since last read: {path}]"
                return pdf_redirect_note + unchanged if pdf_redirect_note else unchanged

            raw = fp.read_bytes()
            if not raw:
                return f"(Empty file: {path})"

            mime = detect_image_mime(raw) or mimetypes.guess_type(path)[0]
            if mime and mime.startswith("image/"):
                return build_image_content_blocks(raw, mime, str(fp), f"(Image file: {path})")

            try:
                text_content = raw.decode("utf-8")
            except UnicodeDecodeError:
                return f"Error: Cannot read binary file {path} (MIME: {mime or 'unknown'}). Only UTF-8 text and images are supported."

            raw_lines = text_content.splitlines()
            plain = _should_emit_plain_text(fp, raw_lines)
            all_lines = (
                [_strip_line_number_prefix(line) for line in raw_lines]
                if _looks_numbered(raw_lines)
                else raw_lines
            )
            result, end, total = _format_text_read(
                all_lines,
                offset=offset,
                limit=limit,
                default_limit=self._DEFAULT_LIMIT,
                max_chars=self._MAX_CHARS,
                plain=plain,
            )
            if result.startswith("Error:"):
                return result

            file_state.record_read(
                fp,
                offset=offset,
                limit=limit,
                variant=dedup_variant if plain_cache else None,
            )
            return pdf_redirect_note + result if pdf_redirect_note else result
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error reading file: {e}"

    def _read_pdf(self, fp: Path, pages: str | None) -> str:
        pdf_variant = pages or f"__default__:{self._MAX_PDF_PAGES}"
        if file_state.is_unchanged(fp, variant=pdf_variant):
            return f"[File unchanged since last read: {fp}]"

        try:
            import fitz  # pymupdf
        except ImportError:
            return "Error: PDF reading requires pymupdf. Install with: pip install pymupdf"

        try:
            doc = fitz.open(str(fp))
        except Exception as e:
            return f"Error reading PDF: {e}"

        total_pages = len(doc)
        if pages:
            try:
                start, end = _parse_page_range(pages, total_pages)
            except (ValueError, IndexError):
                doc.close()
                return f"Error: Invalid page range '{pages}'. Use format like '1-5'."
            if start > end or start >= total_pages:
                doc.close()
                return f"Error: Page range '{pages}' is out of bounds (document has {total_pages} pages)."
        else:
            start = 0
            end = min(total_pages - 1, self._MAX_PDF_PAGES - 1)

        if end - start + 1 > self._MAX_PDF_PAGES:
            end = start + self._MAX_PDF_PAGES - 1

        parts: list[str] = []
        for i in range(start, end + 1):
            page = doc[i]
            text = page.get_text().strip()
            if text:
                parts.append(f"--- Page {i + 1} ---\n{text}")
        doc.close()

        if not parts:
            return f"(PDF has no extractable text: {fp})"

        result = "\n\n".join(parts)
        if end < total_pages - 1:
            result += f"\n\n(Showing pages {start + 1}-{end + 1} of {total_pages}. Use pages='{end + 2}-{min(end + 1 + self._MAX_PDF_PAGES, total_pages)}' to continue.)"
        if len(result) > self._MAX_CHARS:
            result = result[:self._MAX_CHARS] + "\n\n(PDF text truncated at ~128K chars)"
        file_state.record_read(fp, variant=pdf_variant)
        return result


# ---------------------------------------------------------------------------
# 写入文件
# ---------------------------------------------------------------------------


@tool_parameters(
    tool_parameters_schema(
        path=StringSchema("The file path to write to"),
        content=StringSchema("The content to write"),
        required=["path", "content"],
    )
)
class WriteFileTool(_FsTool):
    """将内容写入文件。"""

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return (
            "Write content to a file. Overwrites if the file already exists; "
            "creates parent directories as needed. "
            "One-off scripts must go under temp/ (auto-deleted after the turn). "
            "For partial edits, prefer edit_file instead."
        )

    async def execute(self, path: str | None = None, content: str | None = None, **kwargs: Any) -> str:
        try:
            if not path:
                raise ValueError("Unknown path")
            if content is None:
                raise ValueError("Unknown content")
            fp = self._resolve(path)
            if fp.suffix.lower() in {".docx", ".doc"}:
                return (
                    f"Error: Cannot write Word binary format with write_file ({fp.name}). "
                    "Use docx-mcp instead: mcp_docx-mcp_create_from_markdown (from .md), "
                    "mcp_docx-mcp_create_document, then mcp_docx-mcp_insert_text / replace_text "
                    "and mcp_docx-mcp_save_document. After writing, open_document + get_headings/search_text "
                    "to verify content before telling the user the task is done. "
                    "Plain text or Markdown is not a valid .docx."
                )
            if err := self._guard_scratch_write(fp):
                return err
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(content, encoding="utf-8")
            file_state.record_write(fp)
            self._note_temp_write(fp)
            return f"Successfully wrote {len(content)} characters to {fp}"
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error writing file: {e}"


# ---------------------------------------------------------------------------
# 编辑文件
# ---------------------------------------------------------------------------

_QUOTE_TABLE = str.maketrans({
    "\u2018": "'", "\u2019": "'",  # curly single → straight
    "\u201c": '"', "\u201d": '"',  # curly double → straight
    "'": "'", '"': '"',            # identity (kept for completeness)
})


def _normalize_quotes(s: str) -> str:
    return s.translate(_QUOTE_TABLE)


def _curly_double_quotes(text: str) -> str:
    parts: list[str] = []
    opening = True
    for ch in text:
        if ch == '"':
            parts.append("\u201c" if opening else "\u201d")
            opening = not opening
        else:
            parts.append(ch)
    return "".join(parts)


def _curly_single_quotes(text: str) -> str:
    parts: list[str] = []
    opening = True
    for i, ch in enumerate(text):
        if ch != "'":
            parts.append(ch)
            continue
        prev_ch = text[i - 1] if i > 0 else ""
        next_ch = text[i + 1] if i + 1 < len(text) else ""
        if prev_ch.isalnum() and next_ch.isalnum():
            parts.append("\u2019")
            continue
        parts.append("\u2018" if opening else "\u2019")
        opening = not opening
    return "".join(parts)


def _preserve_quote_style(old_text: str, actual_text: str, new_text: str) -> str:
    """当命中引号归一化回退时，保留弯引号风格。"""
    if _normalize_quotes(old_text.strip()) != _normalize_quotes(actual_text.strip()) or old_text == actual_text:
        return new_text

    styled = new_text
    if any(ch in actual_text for ch in ("\u201c", "\u201d")) and '"' in styled:
        styled = _curly_double_quotes(styled)
    if any(ch in actual_text for ch in ("\u2018", "\u2019")) and "'" in styled:
        styled = _curly_single_quotes(styled)
    return styled


def _leading_ws(line: str) -> str:
    return line[: len(line) - len(line.lstrip(" \t"))]


def _reindent_like_match(old_text: str, actual_text: str, new_text: str) -> str:
    """保留实际匹配代码块的外层缩进。"""
    old_lines = old_text.split("\n")
    actual_lines = actual_text.split("\n")
    if len(old_lines) != len(actual_lines):
        return new_text

    comparable = [
        (old_line, actual_line)
        for old_line, actual_line in zip(old_lines, actual_lines)
        if old_line.strip() and actual_line.strip()
    ]
    if not comparable or any(
        _normalize_quotes(old_line.strip()) != _normalize_quotes(actual_line.strip())
        for old_line, actual_line in comparable
    ):
        return new_text

    old_ws = _leading_ws(comparable[0][0])
    actual_ws = _leading_ws(comparable[0][1])
    if actual_ws == old_ws:
        return new_text

    if old_ws:
        if not actual_ws.startswith(old_ws):
            return new_text
        delta = actual_ws[len(old_ws):]
    else:
        delta = actual_ws

    if not delta:
        return new_text

    return "\n".join((delta + line) if line else line for line in new_text.split("\n"))


@dataclass(slots=True)
class _MatchSpan:
    start: int
    end: int
    text: str
    line: int


def _find_exact_matches(content: str, old_text: str) -> list[_MatchSpan]:
    matches: list[_MatchSpan] = []
    start = 0
    while True:
        idx = content.find(old_text, start)
        if idx == -1:
            break
        matches.append(
            _MatchSpan(
                start=idx,
                end=idx + len(old_text),
                text=content[idx : idx + len(old_text)],
                line=content.count("\n", 0, idx) + 1,
            )
        )
        start = idx + max(1, len(old_text))
    return matches


def _find_trim_matches(content: str, old_text: str, *, normalize_quotes: bool = False) -> list[_MatchSpan]:
    old_lines = old_text.splitlines()
    if not old_lines:
        return []

    content_lines = content.splitlines()
    content_lines_keepends = content.splitlines(keepends=True)
    if len(content_lines) < len(old_lines):
        return []

    offsets: list[int] = []
    pos = 0
    for line in content_lines_keepends:
        offsets.append(pos)
        pos += len(line)
    offsets.append(pos)

    if normalize_quotes:
        stripped_old = [_normalize_quotes(line.strip()) for line in old_lines]
    else:
        stripped_old = [line.strip() for line in old_lines]

    matches: list[_MatchSpan] = []
    window_size = len(stripped_old)
    for i in range(len(content_lines) - window_size + 1):
        window = content_lines[i : i + window_size]
        if normalize_quotes:
            comparable = [_normalize_quotes(line.strip()) for line in window]
        else:
            comparable = [line.strip() for line in window]
        if comparable != stripped_old:
            continue

        start = offsets[i]
        end = offsets[i + window_size]
        if content_lines_keepends[i + window_size - 1].endswith("\n"):
            end -= 1
        matches.append(
            _MatchSpan(
                start=start,
                end=end,
                text=content[start:end],
                line=i + 1,
            )
        )
    return matches


def _find_quote_matches(content: str, old_text: str) -> list[_MatchSpan]:
    norm_content = _normalize_quotes(content)
    norm_old = _normalize_quotes(old_text)
    matches: list[_MatchSpan] = []
    start = 0
    while True:
        idx = norm_content.find(norm_old, start)
        if idx == -1:
            break
        matches.append(
            _MatchSpan(
                start=idx,
                end=idx + len(old_text),
                text=content[idx : idx + len(old_text)],
                line=content.count("\n", 0, idx) + 1,
            )
        )
        start = idx + max(1, len(norm_old))
    return matches


def _find_matches(content: str, old_text: str) -> list[_MatchSpan]:
    """使用逐步放宽策略定位全部匹配项。"""
    for matcher in (
        lambda: _find_exact_matches(content, old_text),
        lambda: _find_trim_matches(content, old_text),
        lambda: _find_trim_matches(content, old_text, normalize_quotes=True),
        lambda: _find_quote_matches(content, old_text),
    ):
        matches = matcher()
        if matches:
            return matches
    return []


def _find_match_line_numbers(content: str, old_text: str) -> list[int]:
    """返回当前匹配策略下以 1 开始的起始行号。"""
    return [match.line for match in _find_matches(content, old_text)]


def _collapse_internal_whitespace(text: str) -> str:
    return "\n".join(" ".join(line.split()) for line in text.splitlines())


def _diagnose_near_match(old_text: str, actual_text: str) -> list[str]:
    """返回可操作提示，说明文本为何接近但不精确匹配。"""
    hints: list[str] = []

    if old_text.lower() == actual_text.lower() and old_text != actual_text:
        hints.append("letter case differs")
    if _collapse_internal_whitespace(old_text) == _collapse_internal_whitespace(actual_text) and old_text != actual_text:
        hints.append("whitespace differs")
    if old_text.rstrip("\n") == actual_text.rstrip("\n") and old_text != actual_text:
        hints.append("trailing newline differs")
    if _normalize_quotes(old_text) == _normalize_quotes(actual_text) and old_text != actual_text:
        hints.append("quote style differs")

    return hints


def _best_window(old_text: str, content: str) -> tuple[float, int, list[str], list[str]]:
    """查找最接近的行窗口匹配并返回相似度/起始行/片段/提示。"""
    lines = content.splitlines(keepends=True)
    old_lines = old_text.splitlines(keepends=True)
    window = max(1, len(old_lines))

    best_ratio, best_start = -1.0, 0
    best_window_lines: list[str] = []

    for i in range(max(1, len(lines) - window + 1)):
        current = lines[i : i + window]
        ratio = difflib.SequenceMatcher(None, old_lines, current).ratio()
        if ratio > best_ratio:
            best_ratio, best_start = ratio, i
            best_window_lines = current

    actual_text = "".join(best_window_lines).replace("\r\n", "\n").rstrip("\n")
    hints = _diagnose_near_match(old_text.replace("\r\n", "\n").rstrip("\n"), actual_text)
    return best_ratio, best_start, best_window_lines, hints


def _find_match(content: str, old_text: str) -> tuple[str | None, int]:
    """在内容中通过多级回退链定位 old_text：

    1. Exact substring match
    2. Line-trimmed sliding window (handles indentation differences)
    3. Smart quote normalization (curly ↔ straight quotes)

    Both inputs should use LF line endings (caller normalises CRLF).
    Returns (matched_fragment, count) or (None, 0).
    """
    matches = _find_matches(content, old_text)
    if not matches:
        return None, 0
    return matches[0].text, len(matches)


@tool_parameters(
    tool_parameters_schema(
        path=StringSchema("The file path to edit"),
        old_text=StringSchema("The text to find and replace"),
        new_text=StringSchema("The text to replace with"),
        replace_all=BooleanSchema(description="Replace all occurrences (default false)"),
        required=["path", "old_text", "new_text"],
    )
)
class EditFileTool(_FsTool):
    """通过回退匹配替换文本来编辑文件。"""

    _MAX_EDIT_FILE_SIZE = 1024 * 1024 * 1024  # 1 GiB
    _MARKDOWN_EXTS = frozenset({".md", ".mdx", ".markdown"})

    @property
    def name(self) -> str:
        return "edit_file"

    @property
    def description(self) -> str:
        return (
            "Edit a file by replacing old_text with new_text. "
            "Tolerates minor whitespace/indentation differences and curly/straight quote mismatches. "
            "One-off scripts must live under temp/ (auto-deleted after the turn). "
            "If old_text matches multiple times, you must provide more context "
            "or set replace_all=true. Shows a diff of the closest match on failure."
        )

    @staticmethod
    def _strip_trailing_ws(text: str) -> str:
        """去除每行行尾空白。"""
        return "\n".join(line.rstrip() for line in text.split("\n"))

    async def execute(
        self, path: str | None = None, old_text: str | None = None,
        new_text: str | None = None,
        replace_all: bool = False, **kwargs: Any,
    ) -> str:
        try:
            if not path:
                raise ValueError("Unknown path")
            if old_text is None:
                raise ValueError("Unknown old_text")
            if new_text is None:
                raise ValueError("Unknown new_text")

            # .ipynb detection
            if path.endswith(".ipynb"):
                return "Error: This is a Jupyter notebook. Use the notebook_edit tool instead of edit_file."

            fp = self._resolve(path)
            if err := self._guard_scratch_write(fp):
                return err

            # 创建文件语义：old_text='' 且文件不存在 -> 创建
            if not fp.exists():
                if old_text == "":
                    fp.parent.mkdir(parents=True, exist_ok=True)
                    fp.write_text(new_text, encoding="utf-8")
                    file_state.record_write(fp)
                    self._note_temp_write(fp)
                    return f"Successfully created {fp}"
                return self._file_not_found_msg(path, fp)

            # 文件大小保护
            try:
                fsize = fp.stat().st_size
            except OSError:
                fsize = 0
            if fsize > self._MAX_EDIT_FILE_SIZE:
                return f"Error: File too large to edit ({fsize / (1024**3):.1f} GiB). Maximum is 1 GiB."

            # 创建文件：old_text='' 但文件已存在且非空 -> 拒绝
            if old_text == "":
                raw = fp.read_bytes()
                content = raw.decode("utf-8")
                if content.strip():
                    return f"Error: Cannot create file — {path} already exists and is not empty."
                fp.write_text(new_text, encoding="utf-8")
                file_state.record_write(fp)
                self._note_temp_write(fp)
                return f"Successfully edited {fp}"

            # 编辑前读取检查
            warning = file_state.check_read(fp)

            raw = fp.read_bytes()
            uses_crlf = b"\r\n" in raw
            content = raw.decode("utf-8").replace("\r\n", "\n")
            norm_old = old_text.replace("\r\n", "\n")
            matches = _find_matches(content, norm_old)

            if not matches:
                return self._not_found_msg(old_text, content, path)
            count = len(matches)
            if count > 1 and not replace_all:
                line_numbers = [match.line for match in matches]
                preview = ", ".join(f"line {n}" for n in line_numbers[:3])
                if len(line_numbers) > 3:
                    preview += ", ..."
                location_hint = f" at {preview}" if preview else ""
                return (
                    f"Warning: old_text appears {count} times{location_hint}. "
                    "Provide more context to make it unique, or set replace_all=true."
                )

            norm_new = new_text.replace("\r\n", "\n")

            # 行尾空白清理（Markdown 跳过，保留双空格换行语义）
            if fp.suffix.lower() not in self._MARKDOWN_EXTS:
                norm_new = self._strip_trailing_ws(norm_new)

            selected = matches if replace_all else matches[:1]
            new_content = content
            for match in reversed(selected):
                replacement = _preserve_quote_style(norm_old, match.text, norm_new)
                replacement = _reindent_like_match(norm_old, match.text, replacement)

                # 删除行清理：当删除文本（new_text=''）时，吞并后续
                # 换行，避免残留空白行
                end = match.end
                if replacement == "" and not match.text.endswith("\n") and content[end:end + 1] == "\n":
                    end += 1

                new_content = new_content[: match.start] + replacement + new_content[end:]
            if uses_crlf:
                new_content = new_content.replace("\n", "\r\n")

            fp.write_bytes(new_content.encode("utf-8"))
            file_state.record_write(fp)
            self._note_temp_write(fp)
            msg = f"Successfully edited {fp}"
            if warning:
                msg = f"{warning}\n{msg}"
            return msg
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error editing file: {e}"

    def _file_not_found_msg(self, path: str, fp: Path) -> str:
        """构建带有“Did you mean ...?”建议的错误信息。"""
        parent = fp.parent
        suggestions: list[str] = []
        if parent.is_dir():
            siblings = [f.name for f in parent.iterdir() if f.is_file()]
            close = difflib.get_close_matches(fp.name, siblings, n=3, cutoff=0.6)
            suggestions = [str(parent / c) for c in close]
        parts = [f"Error: File not found: {path}"]
        if suggestions:
            parts.append("Did you mean: " + ", ".join(suggestions) + "?")
        return "\n".join(parts)

    @staticmethod
    def _not_found_msg(old_text: str, content: str, path: str) -> str:
        best_ratio, best_start, best_window_lines, hints = _best_window(old_text, content)
        if best_ratio > 0.5:
            diff = "\n".join(difflib.unified_diff(
                old_text.splitlines(keepends=True),
                best_window_lines,
                fromfile="old_text (provided)",
                tofile=f"{path} (actual, line {best_start + 1})",
                lineterm="",
            ))
            hint_text = ""
            if hints:
                hint_text = "\nPossible cause: " + ", ".join(hints) + "."
            return (
                f"Error: old_text not found in {path}."
                f"{hint_text}\nBest match ({best_ratio:.0%} similar) at line {best_start + 1}:\n{diff}"
            )

        if hints:
            return (
                f"Error: old_text not found in {path}. "
                f"Possible cause: {', '.join(hints)}. "
                "Copy the exact text from read_file and try again."
            )
        return f"Error: old_text not found in {path}. No similar text found. Verify the file content."


# ---------------------------------------------------------------------------
# 列出目录
# ---------------------------------------------------------------------------

@tool_parameters(
    tool_parameters_schema(
        path=StringSchema("The directory path to list"),
        recursive=BooleanSchema(description="Recursively list all files (default false)"),
        max_entries=IntegerSchema(
            200,
            description="Maximum entries to return (default 200)",
            minimum=1,
        ),
        required=["path"],
    )
)
class ListDirTool(_FsTool):
    """列出目录内容，可选递归。"""

    _DEFAULT_MAX = 200
    _IGNORE_DIRS = {
        ".git", "node_modules", "__pycache__", ".venv", "venv",
        "dist", "build", ".tox", ".mypy_cache", ".pytest_cache",
        ".ruff_cache", ".coverage", "htmlcov",
    }

    @property
    def name(self) -> str:
        return "list_dir"

    @property
    def description(self) -> str:
        return (
            "List the contents of a directory. "
            "Set recursive=true to explore nested structure. "
            "Common noise directories (.git, node_modules, __pycache__, etc.) are auto-ignored."
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(
        self, path: str | None = None, recursive: bool = False,
        max_entries: int | None = None, **kwargs: Any,
    ) -> str:
        try:
            if path is None:
                raise ValueError("Unknown path")
            dp = self._resolve(path)
            if not dp.exists():
                return f"Error: Directory not found: {path}"
            if not dp.is_dir():
                return f"Error: Not a directory: {path}"

            cap = max_entries or self._DEFAULT_MAX
            items: list[str] = []
            total = 0

            if recursive:
                for item in sorted(dp.rglob("*")):
                    if any(p in self._IGNORE_DIRS for p in item.parts):
                        continue
                    total += 1
                    if len(items) < cap:
                        rel = item.relative_to(dp)
                        items.append(f"{rel}/" if item.is_dir() else str(rel))
            else:
                for item in sorted(dp.iterdir()):
                    if item.name in self._IGNORE_DIRS:
                        continue
                    total += 1
                    if len(items) < cap:
                        pfx = "📁 " if item.is_dir() else "📄 "
                        items.append(f"{pfx}{item.name}")

            if not items and total == 0:
                return f"Directory {path} is empty"

            result = "\n".join(items)
            if total > cap:
                result += f"\n\n(truncated, showing first {cap} of {total} entries)"
            return result
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error listing directory: {e}"
