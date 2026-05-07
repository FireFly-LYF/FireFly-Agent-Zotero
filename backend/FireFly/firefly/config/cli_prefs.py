"""与 ``config.json`` 同目录的 ``cli.json``：本地 CLI 偏好（非密钥）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

LLM_INPUT_PRINT_KEYS = ("tools", "rag", "skills", "user", "system")

# 无 cli.json 文件时的内存默认（安静、整段脱敏）
DEFAULT_CLI_PREFS: dict[str, Any] = {
    "show_llm_input": False,
}

# onboard 首次写入磁盘时的模板（与仓库 backend/config/cli.json 一致）
DEFAULT_CLI_FILE_BOOTSTRAP: dict[str, Any] = {
    "show_llm_input": True,
    "llm_input_print": {
        "system": True,
        "user": True,
        "rag": True,
        "skills": True,
        "tools": True,
    },
}


def cli_prefs_path_for(config_path: Path) -> Path:
    """``cli.json`` 与给定 ``config.json`` 位于同一目录。"""
    p = Path(config_path).expanduser().resolve()
    return p.parent / "cli.json"


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


def load_cli_prefs(config_path: Path | None = None) -> dict[str, Any]:
    """读取 ``cli.json``，缺省或损坏时返回内置默认值。"""
    from firefly.config.loader import get_config_path

    path = cli_prefs_path_for(config_path or get_config_path())
    merged = dict(DEFAULT_CLI_PREFS)
    if not path.is_file():
        return merged
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return merged
    if not isinstance(raw, dict):
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

    return merged


def ensure_cli_prefs_file(config_path: Path) -> Path:
    """若同目录下尚无 ``cli.json``，则写入默认内容（不覆盖已有文件）。"""
    path = cli_prefs_path_for(config_path)
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(DEFAULT_CLI_FILE_BOOTSTRAP, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path
