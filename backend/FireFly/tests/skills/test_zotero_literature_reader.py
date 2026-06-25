"""Tests for zotero_literature_reader."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from firefly.skills.zotero.scripts.zotero_literature_reader import (
    ZoteroReaderError,
    format_item_for_agent,
    read_item,
    resolve_literature_content,
    resolve_llm_wiki_content_roots,
    search_items,
)

_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
_ZOTERO_MINIMAL_SEED_SQL = (_FIXTURES_DIR / "zotero_minimal_seed.sql").read_text(
    encoding="utf-8"
)
_ZOTERO_LEGACY_SEED_SQL = (
    _FIXTURES_DIR / "zotero_legacy_parent_on_items_seed.sql"
).read_text(encoding="utf-8")


def _seed_minimal_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(_ZOTERO_MINIMAL_SEED_SQL)
        conn.commit()
    finally:
        conn.close()


def _seed_llm_wiki(tmp_path: Path) -> tuple[Path, Path]:
    wiki_root = tmp_path / "llm-wiki"
    markdown_root = wiki_root / "raw" / "markdown" / "未分类"
    pdf_root = wiki_root / "raw" / "pdf" / "未分类"
    (wiki_root / "wiki").mkdir(parents=True)
    markdown_root.mkdir(parents=True)
    pdf_root.mkdir(parents=True)
    (markdown_root / "PDFKEY01_paper.md").write_text("# Markdown body\n", encoding="utf-8")
    return wiki_root, markdown_root


@pytest.fixture
def zotero_data_dir(tmp_path: Path) -> Path:
    data_dir = tmp_path / "Zotero"
    data_dir.mkdir()
    _seed_minimal_db(data_dir / "zotero.sqlite")
    return data_dir


def test_read_item_by_id(zotero_data_dir: Path) -> None:
    payload = read_item(item_id=100, data_dir=zotero_data_dir)
    assert payload["key"] == "ABCD1234"
    assert payload["fields"]["title"] == "Attention Is All You Need"
    assert payload["creators"][0]["name"] == "Ashish Vaswani"
    assert payload["tags"] == ["transformer"]
    assert payload["notes"][0]["text"] == "User note text"
    assert payload["annotations"][0]["text"] == "scaled dot-product"


def test_read_item_by_key(zotero_data_dir: Path) -> None:
    payload = read_item(item_key="ABCD1234", data_dir=zotero_data_dir)
    assert payload["item_id"] == 100


def test_search_items(zotero_data_dir: Path) -> None:
    hits = search_items("attention", limit=5, data_dir=zotero_data_dir)
    assert len(hits) == 1
    assert hits[0]["item_id"] == 100
    assert hits[0]["title"] == "Attention Is All You Need"


def test_search_and_notes_with_legacy_items_parent_item_id(tmp_path: Path) -> None:
    """Backward compat when parentItemID lives on items (early fixtures)."""
    data_dir = tmp_path / "ZoteroLegacy"
    data_dir.mkdir()
    conn = sqlite3.connect(data_dir / "zotero.sqlite")
    try:
        conn.executescript(_ZOTERO_LEGACY_SEED_SQL)
        conn.commit()
    finally:
        conn.close()

    hits = search_items("legacy", limit=5, data_dir=data_dir)
    assert len(hits) == 1
    assert hits[0]["item_id"] == 100

    payload = read_item(item_id=100, data_dir=data_dir)
    assert payload["notes"][0]["text"] == "Legacy note body"


def test_read_item_missing(zotero_data_dir: Path) -> None:
    with pytest.raises(ZoteroReaderError):
        read_item(item_id=999, data_dir=zotero_data_dir)


def test_format_item_for_agent(zotero_data_dir: Path) -> None:
    payload = read_item(item_id=100, data_dir=zotero_data_dir)
    text = format_item_for_agent(payload)
    assert "Attention Is All You Need" in text
    assert "transformer" in text


def test_literature_prefers_llm_wiki_markdown(
    zotero_data_dir: Path, tmp_path: Path
) -> None:
    wiki_root, _ = _seed_llm_wiki(tmp_path)
    payload = read_item(
        item_id=100,
        data_dir=zotero_data_dir,
        workspace=wiki_root.parent,
        include_storage_text=True,
    )
    lit = payload["literature"]
    assert lit["source"] == "llm-wiki/markdown"
    assert lit["text"] == "# Markdown body\n"
    assert "PDFKEY01_paper.md" in str(lit["path"])


def test_literature_falls_back_to_llm_wiki_pdf(
    zotero_data_dir: Path, tmp_path: Path
) -> None:
    wiki_root = tmp_path / "llm-wiki"
    pdf_root = wiki_root / "raw" / "pdf" / "未分类"
    (wiki_root / "wiki").mkdir(parents=True)
    pdf_root.mkdir(parents=True)
    pdf_path = pdf_root / "PDFKEY01_paper.pdf"
    try:
        import fitz

        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "PDF fallback text")
        doc.save(str(pdf_path))
        doc.close()
    except ImportError:
        pytest.skip("pymupdf unavailable")

    payload = read_item(
        item_id=100,
        data_dir=zotero_data_dir,
        workspace=wiki_root.parent,
        include_storage_text=True,
    )
    lit = payload["literature"]
    assert lit["source"] == "llm-wiki/pdf"
    assert "PDF fallback text" in str(lit.get("text") or "")


def test_resolve_llm_wiki_content_roots_from_repo_layout(tmp_path: Path) -> None:
    wiki_root = tmp_path / "backend" / "llm-wiki"
    (wiki_root / "wiki").mkdir(parents=True)
    (wiki_root / "raw" / "markdown").mkdir(parents=True)
    roots = resolve_llm_wiki_content_roots(tmp_path)
    assert roots is not None
    assert roots[0] == wiki_root.resolve()


def test_resolve_literature_content_zotero_storage(zotero_data_dir: Path) -> None:
    lit = resolve_literature_content(
        attachments=[{"path": "missing", "content_type": "application/pdf"}],
        lookup_keys=["ABCD1234"],
        workspace=None,
        data_dir=zotero_data_dir,
        include_text=False,
    )
    assert lit["source"] is None


def test_cli_json_output(zotero_data_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from firefly.skills.zotero.scripts import zotero_literature_reader as mod

    rc = mod.main(["--data-dir", str(zotero_data_dir), "--item-id", "100"])
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["item_id"] == 100
