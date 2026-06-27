"""RED: generate_change_summary() — email-ready .txt of w:ins/w:del in open doc.

Method must be added to DocxDocument. These tests fail until implemented.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from lxml import etree

from docx_mcp.document import W14, DocxDocument, W

_DATE = "2026-05-01T00:00:00Z"


def _make_doc(tmp_path):
    return DocxDocument.create(str(tmp_path / "test.docx"))


def _inject_ins(doc: DocxDocument, text: str, author: str = "Claude", cid: int = 1) -> None:
    """Inject a paragraph containing a single w:ins into the body."""
    tree = doc._tree("word/document.xml")
    body = tree.find(f"{W}body")
    p = etree.Element(f"{W}p")
    p.set(f"{W14}paraId", uuid.uuid4().hex[:8].upper())
    ins = etree.SubElement(p, f"{W}ins")
    ins.set(f"{W}id", str(cid))
    ins.set(f"{W}author", author)
    ins.set(f"{W}date", _DATE)
    r = etree.SubElement(ins, f"{W}r")
    t = etree.SubElement(r, f"{W}t")
    t.text = text
    body.insert(len(body) - 1, p)
    doc._mark("word/document.xml")


def _inject_del(doc: DocxDocument, text: str, author: str = "Claude", cid: int = 2) -> None:
    """Inject a paragraph containing a single w:del into the body."""
    tree = doc._tree("word/document.xml")
    body = tree.find(f"{W}body")
    p = etree.Element(f"{W}p")
    p.set(f"{W14}paraId", uuid.uuid4().hex[:8].upper())
    del_el = etree.SubElement(p, f"{W}del")
    del_el.set(f"{W}id", str(cid))
    del_el.set(f"{W}author", author)
    del_el.set(f"{W}date", _DATE)
    r = etree.SubElement(del_el, f"{W}r")
    dt = etree.SubElement(r, f"{W}delText")
    dt.text = text
    body.insert(len(body) - 1, p)
    doc._mark("word/document.xml")


def _inject_replacement(
    doc: DocxDocument,
    old_text: str,
    new_text: str,
    author: str = "Alice",
) -> None:
    """Inject adjacent w:del + w:ins in one paragraph (a replacement)."""
    tree = doc._tree("word/document.xml")
    body = tree.find(f"{W}body")
    p = etree.Element(f"{W}p")
    p.set(f"{W14}paraId", uuid.uuid4().hex[:8].upper())
    del_el = etree.SubElement(p, f"{W}del")
    del_el.set(f"{W}id", "10")
    del_el.set(f"{W}author", author)
    del_el.set(f"{W}date", _DATE)
    r1 = etree.SubElement(del_el, f"{W}r")
    dt = etree.SubElement(r1, f"{W}delText")
    dt.text = old_text
    ins_el = etree.SubElement(p, f"{W}ins")
    ins_el.set(f"{W}id", "11")
    ins_el.set(f"{W}author", author)
    ins_el.set(f"{W}date", _DATE)
    r2 = etree.SubElement(ins_el, f"{W}r")
    t2 = etree.SubElement(r2, f"{W}t")
    t2.text = new_text
    body.insert(len(body) - 1, p)
    doc._mark("word/document.xml")


class TestGenerateChangeSummary:
    def test_insertion_text_in_output(self, tmp_path):
        doc = _make_doc(tmp_path)
        _inject_ins(doc, "newly added content")
        result = doc.generate_change_summary(str(tmp_path / "out.txt"))
        assert os.path.exists(result["path"])
        content = Path(result["path"]).read_text()
        assert "newly added content" in content

    def test_deletion_text_in_output(self, tmp_path):
        doc = _make_doc(tmp_path)
        _inject_del(doc, "removed content")
        result = doc.generate_change_summary(str(tmp_path / "out.txt"))
        content = Path(result["path"]).read_text()
        assert "removed content" in content

    def test_change_count_matches_entries(self, tmp_path):
        doc = _make_doc(tmp_path)
        _inject_ins(doc, "item one", cid=1)
        _inject_del(doc, "item two", cid=2)
        result = doc.generate_change_summary(str(tmp_path / "out.txt"))
        assert result["change_count"] == 2

    def test_zero_changes(self, tmp_path):
        doc = _make_doc(tmp_path)
        result = doc.generate_change_summary(str(tmp_path / "out.txt"))
        assert result["change_count"] == 0
        assert os.path.exists(result["path"])

    def test_default_output_path_auto_generated(self, tmp_path):
        doc = _make_doc(tmp_path)
        _inject_ins(doc, "something")
        result = doc.generate_change_summary()
        # Should end in _changes.txt derived from the doc path
        assert result["path"].endswith("_changes.txt")
        assert os.path.exists(result["path"])

    def test_adjacent_del_ins_reported_as_replacement(self, tmp_path):
        doc = _make_doc(tmp_path)
        _inject_replacement(doc, "old value", "new value")
        result = doc.generate_change_summary(str(tmp_path / "out.txt"))
        content = Path(result["path"]).read_text()
        assert "old value" in content
        assert "new value" in content
        # Replacement must be clearly labelled (case-insensitive)
        assert any(kw in content.lower() for kw in ("replacement", "replaced", "replace"))

    def test_author_included_in_output(self, tmp_path):
        doc = _make_doc(tmp_path)
        _inject_ins(doc, "text by Bob", author="Bob")
        result = doc.generate_change_summary(str(tmp_path / "out.txt"))
        content = Path(result["path"]).read_text()
        assert "Bob" in content

    def test_returns_path_and_count_keys(self, tmp_path):
        doc = _make_doc(tmp_path)
        result = doc.generate_change_summary(str(tmp_path / "out.txt"))
        assert "path" in result
        assert "change_count" in result
