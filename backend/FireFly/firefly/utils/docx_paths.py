"""Resolve user/agent .docx paths against the backend docx deliverables directory."""

from __future__ import annotations

from pathlib import Path

from firefly.config.paths import get_docx_dir

_DOCX_SUFFIXES = frozenset({".docx", ".dotx"})
_DOCX_PATH_ARG_KEYS = frozenset({"path", "output_path", "template_path"})


def is_docx_delivery_path(path: str) -> bool:
    """True when a non-absolute path should resolve under ``llm-wiki/docx/``."""
    raw = str(path or "").strip().strip('"')
    if not raw:
        return False
    p = Path(raw).expanduser()
    if p.is_absolute():
        return False
    norm = raw.replace("\\", "/").lower()
    if norm == "docx" or norm.startswith("docx/"):
        return True
    if norm.startswith("llm-wiki/docx/") or norm == "llm-wiki/docx":
        return True
    return p.suffix.lower() in _DOCX_SUFFIXES


def _relative_under_docx_dir(raw: str) -> Path:
    """Strip optional ``docx/`` or ``llm-wiki/docx/`` prefix; return path relative to docx root."""
    norm = raw.replace("\\", "/")
    lower = norm.lower()
    if lower.startswith("llm-wiki/docx/"):
        tail = norm[len("llm-wiki/docx/") :]
        return Path(tail) if tail else Path(".")
    p = Path(raw)
    parts = p.parts
    if parts and parts[0].lower() == "docx":
        return Path(*parts[1:]) if len(parts) > 1 else Path(".")
    return p


def resolve_docx_path(path: str) -> Path:
    """Map relative docx paths to ``llm-wiki/docx/``; leave absolute paths unchanged."""
    raw = str(path or "").strip().strip('"')
    if not raw:
        raise ValueError("docx path is empty")
    p = Path(raw).expanduser()
    if p.is_absolute():
        return p.resolve()

    rel = _relative_under_docx_dir(raw)
    return (get_docx_dir() / rel).resolve()


def apply_docx_path_resolution(kwargs: dict[str, object]) -> dict[str, object]:
    """Rewrite docx-mcp path arguments to absolute paths under backend docx dir."""
    out = dict(kwargs)
    for key in _DOCX_PATH_ARG_KEYS:
        val = out.get(key)
        if not isinstance(val, str) or not val.strip():
            continue
        if is_docx_delivery_path(val):
            out[key] = str(resolve_docx_path(val))
    return out
