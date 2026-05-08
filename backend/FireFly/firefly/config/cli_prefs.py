"""与 ``config.json`` 同目录的 ``user.json``：本地偏好（非密钥）。"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

_USER_PREFS_FILENAME = "user.json"

LLM_INPUT_PRINT_KEYS = ("tools", "rag", "skills", "user", "system")

# Markdown → RAG 切片（与 ``markdown_to_rag.py`` 一致）
DEFAULT_MARKDOWN_RAG: dict[str, Any] = {
    "max_chars": 1600,
    "show_chunk_preview_limit": 5,
}

# 无 ``user.json`` 时的内存默认（安静、整段脱敏）
DEFAULT_CLI_PREFS: dict[str, Any] = {
    "show_llm_input": False,
    "markdown_rag": copy.deepcopy(DEFAULT_MARKDOWN_RAG),
}

# onboard / 首次写入磁盘时的模板（写入 ``user.json``）
DEFAULT_CLI_FILE_BOOTSTRAP: dict[str, Any] = {
    "show_llm_input": True,
    "llm_input_print": {
        "system": True,
        "user": True,
        "rag": True,
        "skills": True,
        "tools": True,
    },
    "markdown_rag": copy.deepcopy(DEFAULT_MARKDOWN_RAG),
}


def cli_prefs_path_for(config_path: Path) -> Path:
    """``user.json`` 与给定 ``config.json`` 同目录。"""
    p = Path(config_path).expanduser().resolve()
    return p.parent / _USER_PREFS_FILENAME


def _load_user_json_raw(config_path: Path) -> dict[str, Any]:
    path = Path(config_path).expanduser().resolve().parent / _USER_PREFS_FILENAME
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def _parse_bool(val: Any) -> bool | None:
    if val is None:
        return None
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    if isinstance(val, str):
        s = val.strip().lower()
        if s in ("1", "true", "yes", "on"):
            return True
        if s in ("0", "false", "no", "off", ""):
            return False
    return None


def _parse_positive_int(val: Any, *, default: int, minimum: int | None = None) -> int:
    try:
        if val is None:
            return default
        n = int(val)
        if minimum is not None:
            n = max(minimum, n)
        return n
    except (TypeError, ValueError):
        return default


def load_cli_prefs(config_path: Path | None = None) -> dict[str, Any]:
    """读取 ``user.json``；缺省或损坏字段回退到内置默认值。"""
    from firefly.config.loader import get_config_path

    cfg = Path(config_path or get_config_path()).expanduser().resolve()
    merged: dict[str, Any] = copy.deepcopy(DEFAULT_CLI_PREFS)

    raw = _load_user_json_raw(cfg)
    if not raw:
        return merged

    val = raw.get("show_llm_input", raw.get("showLlmInput"))
    b = _parse_bool(val)
    if b is not None:
        merged["show_llm_input"] = b

    lip = raw.get("llm_input_print", raw.get("llmInputPrint"))
    if isinstance(lip, dict):
        flags: dict[str, bool] = {}
        for k in LLM_INPUT_PRINT_KEYS:
            bv = _parse_bool(lip.get(k))
            flags[k] = bool(bv) if bv is not None else False
        merged["llm_input_print"] = flags

    raw_mr = raw.get("markdown_rag", raw.get("markdownRag"))
    mr_out = copy.deepcopy(DEFAULT_MARKDOWN_RAG)
    if isinstance(raw_mr, dict):
        if raw_mr.get("max_chars") is not None or raw_mr.get("maxChars") is not None:
            v = raw_mr.get("max_chars", raw_mr.get("maxChars"))
            mr_out["max_chars"] = _parse_positive_int(v, default=mr_out["max_chars"], minimum=200)
        if (
            raw_mr.get("show_chunk_preview_limit") is not None
            or raw_mr.get("showChunkPreviewLimit") is not None
        ):
            v = raw_mr.get("show_chunk_preview_limit", raw_mr.get("showChunkPreviewLimit"))
            mr_out["show_chunk_preview_limit"] = _parse_positive_int(
                v, default=mr_out["show_chunk_preview_limit"], minimum=1
            )
    merged["markdown_rag"] = mr_out

    return merged


def get_markdown_rag_prefs(config_path: Path | None = None) -> dict[str, int]:
    """返回已解析的 Markdown→RAG 数值配置（供脚本与索引任务使用）。"""
    p = load_cli_prefs(config_path)
    mr = p.get("markdown_rag")
    if not isinstance(mr, dict):
        mr = DEFAULT_MARKDOWN_RAG
    return {
        "max_chars": max(200, int(mr.get("max_chars", DEFAULT_MARKDOWN_RAG["max_chars"]))),
        "show_chunk_preview_limit": max(
            1, int(mr.get("show_chunk_preview_limit", DEFAULT_MARKDOWN_RAG["show_chunk_preview_limit"]))
        ),
    }


def ensure_cli_prefs_file(config_path: Path) -> Path:
    """若尚无 ``user.json``，则写入默认内容（不覆盖已有文件）。"""
    path = cli_prefs_path_for(config_path)
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(DEFAULT_CLI_FILE_BOOTSTRAP, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path
