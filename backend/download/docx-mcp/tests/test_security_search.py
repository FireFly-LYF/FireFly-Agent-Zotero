"""Security tests for search_text ReDoS protection."""

from __future__ import annotations

from pathlib import Path

import pytest

from docx_mcp.document import DocxDocument


def _open_doc(tmp_path: Path) -> DocxDocument:
    from tests.conftest import _build_fixture

    path = tmp_path / "test.docx"
    _build_fixture(path)
    doc = DocxDocument(str(path))
    doc.open()
    return doc


def test_invalid_regex_raises_value_error(tmp_path: Path):
    """search_text with invalid regex raises ValueError."""
    doc = _open_doc(tmp_path)
    with pytest.raises(ValueError, match="regex"):
        doc.search_text("[unclosed", regex=True)
    doc.close()


def test_valid_regex_works(tmp_path: Path):
    """search_text with a valid regex returns results."""
    doc = _open_doc(tmp_path)
    results = doc.search_text(r"\w+", regex=True)
    assert isinstance(results, list)
    doc.close()


def test_regex_no_match_returns_empty(tmp_path: Path):
    """search_text with regex that doesn't match returns empty list."""
    doc = _open_doc(tmp_path)
    results = doc.search_text(r"XYZZY_NOMATCH_12345", regex=True)
    assert results == []
    doc.close()


def test_literal_pipe_or_match(tmp_path: Path):
    """Non-regex ``|`` splits into OR tokens."""
    doc = _open_doc(tmp_path)
    results = doc.search_text("Para|nonexistent")
    assert len(results) >= 1
    doc.close()


def test_literal_cjk_keywords_or_match(tmp_path: Path):
    """Whitespace-separated CJK keywords use OR semantics."""
    path = tmp_path / "cjk.docx"
    W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    W14 = "http://schemas.microsoft.com/office/word/2010/wordml"
    body = (
        f'<w:p xmlns:w="{W}" xmlns:w14="{W14}" w14:paraId="11111111" w14:textId="1">'
        f"<w:r><w:t>步骤1：YOLO检测时频分布</w:t></w:r></w:p>"
    )
    document_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="{W}" xmlns:w14="{W14}">
  <w:body>{body}<w:sectPr/></w:body>
</w:document>"""
    import zipfile

    content_types = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml"
    ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""
    top_rels = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1"
    Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
    Target="word/document.xml"/>
</Relationships>"""
    doc_rels = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>"""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", top_rels)
        zf.writestr("word/_rels/document.xml.rels", doc_rels)
        zf.writestr("word/document.xml", document_xml)

    doc = DocxDocument(str(path))
    doc.open()
    results = doc.search_text("步骤 方法 检测 优化 抑制 重构")
    assert len(results) >= 1
    assert results[0]["matches"]
    matched_tokens = {m["token"] for m in results[0]["matches"]}
    assert "步骤" in matched_tokens
    assert "检测" in matched_tokens
    doc.close()
