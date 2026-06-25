"""跟踪文件读取状态，用于编辑前警告与读取去重。"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class ReadState:
    mtime: float
    offset: int
    limit: int | None
    variant: str | None
    content_hash: str | None
    can_dedup: bool


_state: dict[str, ReadState] = {}


def _hash_file(p: str) -> str | None:
    try:
        return hashlib.sha256(Path(p).read_bytes()).hexdigest()
    except OSError:
        return None


def record_read(
    path: str | Path,
    offset: int = 1,
    limit: int | None = None,
    *,
    variant: str | None = None,
) -> None:
    """记录文件已读取（成功读取后调用）。"""
    p = str(Path(path).resolve())
    try:
        mtime = os.path.getmtime(p)
    except OSError:
        return
    _state[p] = ReadState(
        mtime=mtime,
        offset=offset,
        limit=limit,
        variant=variant,
        content_hash=_hash_file(p),
        can_dedup=True,
    )


def record_write(path: str | Path) -> None:
    """记录文件已写入（同步更新状态中的 mtime）。"""
    p = str(Path(path).resolve())
    try:
        mtime = os.path.getmtime(p)
    except OSError:
        _state.pop(p, None)
        return
    _state[p] = ReadState(
        mtime=mtime,
        offset=1,
        limit=None,
        variant=None,
        content_hash=_hash_file(p),
        can_dedup=False,
    )


def check_read(path: str | Path) -> str | None:
    """检查文件是否已读取且状态仍新鲜。

    Returns None if OK, or a warning string.
    When mtime changed but file content is identical (e.g. touch, editor save),
    the check passes to avoid false-positive staleness warnings.
    """
    p = str(Path(path).resolve())
    entry = _state.get(p)
    if entry is None:
        return "Warning: file has not been read yet. Read it first to verify content before editing."
    try:
        current_mtime = os.path.getmtime(p)
    except OSError:
        return None
    if current_mtime != entry.mtime:
        if entry.content_hash and _hash_file(p) == entry.content_hash:
            entry.mtime = current_mtime
            return None
        return "Warning: file has been modified since last read. Re-read to verify content before editing."
    return None


def is_unchanged(
    path: str | Path,
    offset: int = 1,
    limit: int | None = None,
    *,
    variant: str | None = None,
) -> bool:
    """若文件曾以相同参数读取且 mtime 未变化，则返回 True。"""
    p = str(Path(path).resolve())
    entry = _state.get(p)
    if entry is None:
        return False
    if not entry.can_dedup:
        return False
    if entry.variant != variant:
        return False
    if entry.offset != offset or entry.limit != limit:
        return False
    try:
        current_mtime = os.path.getmtime(p)
    except OSError:
        return False
    return current_mtime == entry.mtime


def clear() -> None:
    """清空全部跟踪状态（便于测试）。"""
    _state.clear()
