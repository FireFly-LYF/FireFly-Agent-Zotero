"""Agent 临时文件：仅允许落在 workspace/temp，单轮对话结束后清理。"""

from __future__ import annotations

import contextvars
from pathlib import Path

from loguru import logger

_SCRATCH_EXTENSIONS = frozenset({".py", ".sh", ".bat", ".ps1", ".js", ".ts", ".rb", ".pl"})
_BRIDGE_TEMP_PREFIX = "zotero-"

_tracked_temp_files: contextvars.ContextVar[set[str] | None] = contextvars.ContextVar(
    "tracked_temp_files",
    default=None,
)


def get_agent_temp_dir(workspace: Path) -> Path:
    """返回并确保 ``workspace/temp`` 存在。"""
    root = Path(workspace).expanduser().resolve()
    temp_dir = root / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    return temp_dir


def get_exec_cache_dir(workspace: Path) -> Path:
    """``exec`` 内部 python -c 转脚本的缓存目录（位于 temp 下）。"""
    cache = get_agent_temp_dir(workspace) / ".exec-cache"
    cache.mkdir(parents=True, exist_ok=True)
    return cache


def is_under_agent_temp(path: Path, workspace: Path) -> bool:
    try:
        path.expanduser().resolve().relative_to(get_agent_temp_dir(workspace))
        return True
    except (ValueError, OSError):
        return False


def is_bridge_temp_file(path: Path) -> bool:
    return path.name.startswith(_BRIDGE_TEMP_PREFIX)


def validate_scratch_write_path(path: Path, workspace: Path | None) -> str | None:
    """一次性脚本/命令文件必须写在 ``workspace/temp`` 下。"""
    if workspace is None:
        return None
    try:
        ws = Path(workspace).expanduser().resolve()
        fp = path.expanduser().resolve()
        rel = fp.relative_to(ws)
    except (ValueError, OSError):
        return None

    rel_posix = rel.as_posix()
    if rel_posix.startswith("skills/"):
        return None
    if is_under_agent_temp(fp, ws):
        return None
    if fp.suffix.lower() not in _SCRATCH_EXTENSIONS:
        return None

    temp_dir = get_agent_temp_dir(ws)
    return (
        f"Error: One-off script files ({fp.suffix}) must be written under "
        f"{temp_dir}/ (auto-deleted after this turn). "
        "Use skills/ only for persistent custom skills."
    )


def begin_agent_temp_turn() -> None:
    _tracked_temp_files.set(set())


def register_agent_temp_file(path: Path, workspace: Path | None) -> None:
    if workspace is None:
        return
    try:
        fp = path.expanduser().resolve()
    except OSError:
        return
    if not is_under_agent_temp(fp, Path(workspace)):
        return
    if is_bridge_temp_file(fp):
        return
    tracked = _tracked_temp_files.get()
    if tracked is None:
        tracked = set()
        _tracked_temp_files.set(tracked)
    tracked.add(str(fp))


def cleanup_agent_temp_turn(workspace: Path | None) -> None:
    """删除本轮登记的 ``workspace/temp`` 文件（保留 Zotero bridge 缓存）。"""
    tracked = _tracked_temp_files.get()
    _tracked_temp_files.set(None)
    if not tracked or workspace is None:
        return

    temp_root = get_agent_temp_dir(workspace)
    for raw in sorted(tracked, reverse=True):
        p = Path(raw)
        try:
            if not p.is_file():
                continue
            if not is_under_agent_temp(p, workspace):
                continue
            if is_bridge_temp_file(p):
                continue
            p.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Failed to remove agent temp file {}: {}", p, exc)

    try:
        exec_cache = temp_root / ".exec-cache"
        if exec_cache.is_dir() and not any(exec_cache.iterdir()):
            exec_cache.rmdir()
    except OSError:
        pass

    try:
        if temp_root.is_dir() and not any(temp_root.iterdir()):
            temp_root.rmdir()
    except OSError:
        pass


def remove_agent_temp_file(path: Path) -> None:
    """立即删除单个临时文件（如 exec 内部脚本）。"""
    try:
        p = path.expanduser().resolve()
        if p.is_file():
            p.unlink(missing_ok=True)
    except OSError as exc:
        logger.debug("Could not remove temp file {}: {}", path, exc)
