"""Tests for backend docx path resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from firefly.utils.docx_paths import (
    apply_docx_path_resolution,
    is_docx_delivery_path,
    resolve_docx_path,
)


def test_is_docx_delivery_path_detects_relative_docx() -> None:
    assert is_docx_delivery_path("docx/report.docx")
    assert is_docx_delivery_path("抗干扰方法总结.docx")
    assert not is_docx_delivery_path(r"D:\backend\docx\a.docx")
    assert not is_docx_delivery_path("readme.md")


def test_resolve_docx_path_relative_and_prefix(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    docx_root = tmp_path / "docx"
    docx_root.mkdir()
    target = docx_root / "抗干扰方法总结.docx"
    target.write_bytes(b"pk")

    monkeypatch.setattr("firefly.utils.docx_paths.get_docx_dir", lambda: docx_root)

    assert resolve_docx_path("docx/抗干扰方法总结.docx") == target.resolve()
    assert resolve_docx_path("抗干扰方法总结.docx") == target.resolve()
    assert resolve_docx_path(str(target)) == target.resolve()


def test_resolve_docx_path_strips_llm_wiki_prefix(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    docx_root = tmp_path / "llm-wiki" / "docx"
    docx_root.mkdir(parents=True)
    target = docx_root / "report.docx"
    target.write_bytes(b"pk")
    monkeypatch.setattr("firefly.utils.docx_paths.get_docx_dir", lambda: docx_root)

    assert resolve_docx_path("llm-wiki/docx/report.docx") == target.resolve()


def test_apply_docx_path_resolution_for_mcp_kwargs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    docx_root = tmp_path / "docx"
    docx_root.mkdir()
    out = docx_root / "out.docx"
    out.write_bytes(b"pk")
    monkeypatch.setattr("firefly.utils.docx_paths.get_docx_dir", lambda: docx_root)

    resolved = apply_docx_path_resolution({"path": "docx/out.docx", "document_handle": ""})
    assert resolved["path"] == str(out.resolve())
    assert resolved["document_handle"] == ""
