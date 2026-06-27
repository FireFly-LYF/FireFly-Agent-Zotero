"""RED: diff_to_text() — compare two DOCX files, produce tracked-change DOCX + plain .txt.

Static method on DocxDocument (delegates compare_documents for the DOCX half).
These tests fail until implemented.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from lxml import etree

from docx_mcp.document import W14, DocxDocument, W

FIXTURES = Path(__file__).parent / "fixtures"  # noqa: F841 (reserved for future corpus tests)


def _make_doc(tmp_path, name: str, paragraphs: list[str]) -> str:
    """Create a minimal DOCX with the given paragraph texts and save it."""
    path = str(tmp_path / name)
    doc = DocxDocument.create(path)
    tree = doc._tree("word/document.xml")
    body = tree.find(f"{W}body")
    for text in paragraphs:
        p = etree.Element(f"{W}p")
        p.set(f"{W14}paraId", uuid.uuid4().hex[:8].upper())
        r = etree.SubElement(p, f"{W}r")
        t = etree.SubElement(r, f"{W}t")
        t.text = text
        body.insert(len(body) - 1, p)
    doc._mark("word/document.xml")
    doc.save(path)
    return path


class TestDiffToText:
    def test_returns_docx_and_text_paths(self, tmp_path):
        base = _make_doc(tmp_path, "base.docx", ["Hello world"])
        revised = _make_doc(tmp_path, "revised.docx", ["Hello Python"])
        result = DocxDocument.diff_to_text(base, revised)
        assert "docx_path" in result
        assert "text_path" in result
        assert os.path.exists(result["docx_path"])
        assert os.path.exists(result["text_path"])

    def test_change_count_key_present(self, tmp_path):
        base = _make_doc(tmp_path, "base.docx", ["Line one", "Line two"])
        revised = _make_doc(tmp_path, "revised.docx", ["Line one changed", "Line two"])
        result = DocxDocument.diff_to_text(base, revised)
        assert "change_count" in result
        assert result["change_count"] >= 1

    def test_deleted_text_in_summary(self, tmp_path):
        base = _make_doc(tmp_path, "base.docx", ["This paragraph will be deleted"])
        revised = _make_doc(tmp_path, "revised.docx", ["Completely different content"])
        result = DocxDocument.diff_to_text(base, revised)
        content = Path(result["text_path"]).read_text()
        assert "This paragraph will be deleted" in content or "deleted" in content.lower()

    def test_inserted_text_in_summary(self, tmp_path):
        base = _make_doc(tmp_path, "base.docx", ["Original paragraph"])
        revised = _make_doc(tmp_path, "revised.docx", ["Original paragraph", "Brand new paragraph"])
        result = DocxDocument.diff_to_text(base, revised)
        content = Path(result["text_path"]).read_text()
        assert "Brand new paragraph" in content

    def test_no_changes_produces_zero_count(self, tmp_path):
        base = _make_doc(tmp_path, "base.docx", ["Identical content"])
        revised = _make_doc(tmp_path, "revised.docx", ["Identical content"])
        result = DocxDocument.diff_to_text(base, revised)
        assert result["change_count"] == 0

    def test_custom_output_paths_honoured(self, tmp_path):
        base = _make_doc(tmp_path, "base.docx", ["Hello"])
        revised = _make_doc(tmp_path, "revised.docx", ["World"])
        docx_out = str(tmp_path / "custom.docx")
        txt_out = str(tmp_path / "custom.txt")
        result = DocxDocument.diff_to_text(base, revised, docx_output=docx_out, text_output=txt_out)
        assert result["docx_path"] == docx_out
        assert result["text_path"] == txt_out
        assert os.path.exists(docx_out)
        assert os.path.exists(txt_out)

    def test_auto_path_derived_from_base(self, tmp_path):
        """When no output paths given, filenames are derived from base stem."""
        base = _make_doc(tmp_path, "myreport.docx", ["First"])
        revised = _make_doc(tmp_path, "myreport_v2.docx", ["Second"])
        result = DocxDocument.diff_to_text(base, revised)
        assert "myreport" in result["docx_path"]
        assert result["text_path"].endswith(".txt")

    def test_text_file_has_human_readable_structure(self, tmp_path):
        """The .txt file must be meaningful enough to paste into an email."""
        base = _make_doc(tmp_path, "base.docx", ["Original text here"])
        revised = _make_doc(tmp_path, "revised.docx", ["Revised text here"])
        result = DocxDocument.diff_to_text(base, revised)
        content = Path(result["text_path"]).read_text()
        assert len(content.strip()) > 0
        # Word-level diff: "Original" is tracked; "text here" is the equal unchanged part.
        assert "Original" in content or "Revised" in content or "replacement" in content.lower()
