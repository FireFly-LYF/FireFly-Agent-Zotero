"""RED: tracked=False opt-out for all 5 editing tools.

When tracked=False, edits must be applied directly to the XML without
emitting w:ins / w:del markup. These tests fail until the parameter is
implemented in tracks.py, tables.py, and headers_footers.py.
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from lxml import etree

from docx_mcp.document import W14, DocxDocument, W

FIXTURES = Path(__file__).parent / "fixtures"


def _make_doc(tmp_path):
    return DocxDocument.create(str(tmp_path / "test.docx"))


def _add_para(doc: DocxDocument, text: str) -> str:
    tree = doc._tree("word/document.xml")
    body = tree.find(f"{W}body")
    para_id = uuid.uuid4().hex[:8].upper()
    p = etree.Element(f"{W}p")
    p.set(f"{W14}paraId", para_id)
    r = etree.SubElement(p, f"{W}r")
    t = etree.SubElement(r, f"{W}t")
    t.text = text
    body.insert(len(body) - 1, p)
    doc._mark("word/document.xml")
    return para_id


def _para_text(doc: DocxDocument, para_id: str) -> str:
    tree = doc._tree("word/document.xml")
    para = doc._find_para(tree, para_id)
    return "".join(t.text or "" for t in para.iter(f"{W}t"))


class TestInsertTextUntracked:
    def test_no_w_ins_element(self, tmp_path):
        doc = _make_doc(tmp_path)
        pid = _add_para(doc, "Hello")
        doc.insert_text(pid, " world", tracked=False)
        tree = doc._tree("word/document.xml")
        para = doc._find_para(tree, pid)
        assert para.find(f".//{W}ins") is None

    def test_text_is_present_in_run(self, tmp_path):
        doc = _make_doc(tmp_path)
        pid = _add_para(doc, "Hello")
        doc.insert_text(pid, " world", tracked=False)
        assert " world" in _para_text(doc, pid)

    def test_start_position_no_w_ins(self, tmp_path):
        doc = _make_doc(tmp_path)
        pid = _add_para(doc, "world")
        doc.insert_text(pid, "Hello ", position="start", tracked=False)
        tree = doc._tree("word/document.xml")
        para = doc._find_para(tree, pid)
        assert para.find(f".//{W}ins") is None
        assert "Hello " in _para_text(doc, pid)

    def test_default_still_tracked(self, tmp_path):
        """Ensure tracked=True is still the default (regression guard)."""
        doc = _make_doc(tmp_path)
        pid = _add_para(doc, "Hello")
        doc.insert_text(pid, " world")
        tree = doc._tree("word/document.xml")
        para = doc._find_para(tree, pid)
        assert para.find(f".//{W}ins") is not None


class TestDeleteTextUntracked:
    def test_no_w_del_element(self, tmp_path):
        doc = _make_doc(tmp_path)
        pid = _add_para(doc, "Hello world")
        doc.delete_text(pid, "world", tracked=False)
        tree = doc._tree("word/document.xml")
        para = doc._find_para(tree, pid)
        assert para.find(f".//{W}del") is None

    def test_text_removed(self, tmp_path):
        doc = _make_doc(tmp_path)
        pid = _add_para(doc, "Hello world")
        doc.delete_text(pid, "world", tracked=False)
        assert "world" not in _para_text(doc, pid)

    def test_default_still_tracked(self, tmp_path):
        doc = _make_doc(tmp_path)
        pid = _add_para(doc, "Hello world")
        doc.delete_text(pid, "world")
        tree = doc._tree("word/document.xml")
        para = doc._find_para(tree, pid)
        assert para.find(f".//{W}del") is not None


class TestReplaceTextUntracked:
    def test_no_tracked_markup(self, tmp_path):
        doc = _make_doc(tmp_path)
        pid = _add_para(doc, "Hello world")
        doc.replace_text(pid, find="world", replace="Python", tracked=False)
        tree = doc._tree("word/document.xml")
        para = doc._find_para(tree, pid)
        assert para.find(f".//{W}del") is None
        assert para.find(f".//{W}ins") is None

    def test_old_text_gone_new_text_present(self, tmp_path):
        doc = _make_doc(tmp_path)
        pid = _add_para(doc, "Hello world")
        doc.replace_text(pid, find="world", replace="Python", tracked=False)
        text = _para_text(doc, pid)
        assert "Python" in text
        assert "world" not in text

    def test_default_still_tracked(self, tmp_path):
        doc = _make_doc(tmp_path)
        pid = _add_para(doc, "Hello world")
        doc.replace_text(pid, find="world", replace="Python")
        tree = doc._tree("word/document.xml")
        para = doc._find_para(tree, pid)
        assert para.find(f".//{W}del") is not None
        assert para.find(f".//{W}ins") is not None


class TestModifyCellUntracked:
    def _doc_with_table(self, tmp_path):
        """Open the mammoth_tables fixture which has a real table."""
        src = str(FIXTURES / "mammoth_tables.docx")
        dest = str(tmp_path / "table.docx")
        shutil.copy(src, dest)
        doc = DocxDocument(dest)
        doc.open()
        return doc

    def test_no_tracked_markup(self, tmp_path):
        doc = self._doc_with_table(tmp_path)
        doc.modify_cell(0, 0, 0, "replacement", tracked=False)
        tree = doc._tree("word/document.xml")
        tbl = tree.find(f".//{W}tbl")
        tc = tbl.find(f".//{W}tc")
        assert tc.find(f".//{W}del") is None
        assert tc.find(f".//{W}ins") is None

    def test_new_text_written_directly(self, tmp_path):
        doc = self._doc_with_table(tmp_path)
        doc.modify_cell(0, 0, 0, "direct content", tracked=False)
        tree = doc._tree("word/document.xml")
        tbl = tree.find(f".//{W}tbl")
        tc = tbl.find(f".//{W}tc")
        cell_text = "".join(t.text or "" for t in tc.iter(f"{W}t"))
        assert "direct content" in cell_text

    def test_default_still_tracked(self, tmp_path):
        doc = self._doc_with_table(tmp_path)
        doc.modify_cell(0, 0, 0, "tracked edit")
        tree = doc._tree("word/document.xml")
        tbl = tree.find(f".//{W}tbl")
        tc = tbl.find(f".//{W}tc")
        assert tc.find(f".//{W}ins") is not None


class TestEditHeaderFooterUntracked:
    def _doc_with_header(self, tmp_path):
        src = str(FIXTURES / "poi_header_footer.docx")
        dest = str(tmp_path / "hf.docx")
        shutil.copy(src, dest)
        doc = DocxDocument(dest)
        doc.open()
        return doc

    def test_no_tracked_markup(self, tmp_path):
        doc = self._doc_with_header(tmp_path)
        headers = doc.get_headers_footers()
        header = next((h for h in headers if h["location"] == "header"), None)
        if header is None or not header["text"].strip():
            return
        old_text = header["text"].strip()[:4]
        doc.edit_header_footer("header", old_text, "XXXX", tracked=False)
        for h in doc.get_headers_footers():
            if h["location"] == "header":
                tree = doc._trees[h["part"]]
                assert tree.find(f".//{W}del") is None
                assert tree.find(f".//{W}ins") is None

    def test_text_replaced_directly(self, tmp_path):
        doc = self._doc_with_header(tmp_path)
        headers = doc.get_headers_footers()
        header = next((h for h in headers if h["location"] == "header"), None)
        if header is None or not header["text"].strip():
            return
        old_text = header["text"].strip()[:4]
        doc.edit_header_footer("header", old_text, "ZZZZ", tracked=False)
        headers_after = doc.get_headers_footers()
        all_text = "".join(h["text"] for h in headers_after if h["location"] == "header")
        assert "ZZZZ" in all_text

    def test_default_still_tracked(self, tmp_path):
        doc = self._doc_with_header(tmp_path)
        headers = doc.get_headers_footers()
        header = next((h for h in headers if h["location"] == "header"), None)
        if header is None or not header["text"].strip():
            return
        old_text = header["text"].strip()[:4]
        doc.edit_header_footer("header", old_text, "TRACKED")
        for h in doc.get_headers_footers():
            if h["location"] == "header":
                tree = doc._trees[h["part"]]
                assert tree.find(f".//{W}del") is not None or tree.find(f".//{W}ins") is not None
