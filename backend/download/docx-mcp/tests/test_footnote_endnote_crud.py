"""Tests for footnote and endnote CRUD operations (update, delete)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from docx_mcp import server


def _j(result: str) -> dict | list:
    return json.loads(result)


# ═══════════════════════════════════════════════════════════════════════════
#  TestFootnoteCRUD
# ═══════════════════════════════════════════════════════════════════════════


class TestFootnoteCRUD:
    @pytest.fixture(autouse=True)
    def _open(self, test_docx: Path):
        server.open_document(str(test_docx))

    def test_update_footnote(self):
        """Update existing footnote #1 text and verify the result."""
        result = _j(server.update_footnote(1, "Updated footnote text."))
        assert result["footnote_id"] == 1
        assert result["text"] == "Updated footnote text."
        # Verify via get_footnotes
        footnotes = _j(server.get_footnotes())
        fn1 = next(f for f in footnotes if f["id"] == 1)
        assert "Updated footnote text." in fn1["text"]

    def test_update_footnote_not_found(self):
        """Updating a non-existent footnote raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            server.update_footnote(999, "Should fail")

    def test_update_footnote_builtin_rejected(self):
        """Updating built-in footnote (id < 1) raises ValueError."""
        with pytest.raises(ValueError):
            server.update_footnote(0, "Should fail")

    def test_delete_footnote_removes_from_xml(self):
        """delete_footnote removes the definition from footnotes.xml."""
        result = _j(server.delete_footnote(1))
        assert result["deleted"] == 1
        footnotes = _j(server.get_footnotes())
        ids = [f["id"] for f in footnotes]
        assert 1 not in ids

    def test_delete_footnote_removes_reference(self):
        """delete_footnote also removes the footnoteReference run in document.xml."""
        server.delete_footnote(1)
        # validate_footnotes should still report valid (no dangling refs)
        validation = _j(server.validate_footnotes())
        assert validation["valid"] is True
        assert 1 not in validation.get("missing_definitions", [])

    def test_delete_footnote_not_found(self):
        """Deleting a non-existent footnote raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            server.delete_footnote(999)

    def test_update_footnote_then_read_back(self):
        """Round-trip: add a new footnote, update it, confirm text changed."""
        add_result = _j(server.add_footnote("00000004", "Initial text"))
        fid = add_result["footnote_id"]
        _j(server.update_footnote(fid, "Revised text"))
        footnotes = _j(server.get_footnotes())
        fn = next(f for f in footnotes if f["id"] == fid)
        assert "Revised text" in fn["text"]

    def test_consecutive_footnotes_produce_comma_delimiter(self):
        """Two add_footnote() calls on the same paragraph insert a superscript comma
        between refs."""
        W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        W14 = "http://schemas.microsoft.com/office/word/2010/wordml"

        r1 = _j(server.add_footnote("00000004", "First source"))
        r2 = _j(server.add_footnote("00000004", "Second source"))
        id1, id2 = r1["footnote_id"], r2["footnote_id"]

        doc_obj = server._docs[server._DEFAULT_HANDLE]
        doc_tree = doc_obj._tree("word/document.xml")

        para = None
        for p in doc_tree.iter(f"{{{W}}}p"):
            if p.get(f"{{{W14}}}paraId") == "00000004":
                para = p
                break
        assert para is not None, "Paragraph 00000004 not found"

        children = list(para)
        # Footnote refs are wrapped in <w:hyperlink w:anchor="_FnN">
        fn_elems = [
            c
            for c in children
            if (c.tag == f"{{{W}}}hyperlink" and list(c.iter(f"{{{W}}}footnoteReference")))
            or (c.tag == f"{{{W}}}r" and c.find(f"{{{W}}}footnoteReference") is not None)
        ]
        assert len(fn_elems) >= 2, "Expected at least two footnote reference elements"

        idx1 = children.index(fn_elems[-2])
        idx2 = children.index(fn_elems[-1])
        assert idx2 == idx1 + 2, "Comma run should sit between the two consecutive fn ref elements"

        between = children[idx1 + 1]
        assert between.tag == f"{{{W}}}r"
        t_el = between.find(f"{{{W}}}t")
        assert t_el is not None and t_el.text == ",", "Separator run must contain a single comma"

        ref1 = next(fn_elems[-2].iter(f"{{{W}}}footnoteReference")).get(f"{{{W}}}id")
        ref2 = next(fn_elems[-1].iter(f"{{{W}}}footnoteReference")).get(f"{{{W}}}id")
        assert ref1 == str(id1)
        assert ref2 == str(id2)

    def test_body_ref_wrapped_in_internal_hyperlink(self):
        """add_footnote wraps the footnoteReference run in <w:hyperlink w:anchor='_FnN'>."""
        W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

        result = _j(server.add_footnote("00000004", "Some source"))
        fid = result["footnote_id"]

        doc_obj = server._docs[server._DEFAULT_HANDLE]
        doc_tree = doc_obj._tree("word/document.xml")

        hyperlinks = [
            el
            for el in doc_tree.iter(f"{{{W}}}hyperlink")
            if list(el.iter(f"{{{W}}}footnoteReference"))
            and el.get(f"{{{W}}}anchor") == f"_Fn{fid}"
        ]
        assert len(hyperlinks) == 1, (
            f"Expected exactly one <w:hyperlink w:anchor='_Fn{fid}'>, found {len(hyperlinks)}"
        )

    def test_footnote_definition_has_bookmark(self):
        """add_footnote adds <w:bookmarkStart w:name='_FnN'/> in the footnote definition."""
        W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

        result = _j(server.add_footnote("00000004", "Source text"))
        fid = result["footnote_id"]

        doc_obj = server._docs[server._DEFAULT_HANDLE]
        fn_tree = doc_obj._tree("word/footnotes.xml")

        target_fn = next(
            fn for fn in fn_tree.findall(f"{{{W}}}footnote") if fn.get(f"{{{W}}}id") == str(fid)
        )
        bookmarks = [
            el
            for el in target_fn.iter(f"{{{W}}}bookmarkStart")
            if el.get(f"{{{W}}}name") == f"_Fn{fid}"
        ]
        assert len(bookmarks) == 1, f"Expected bookmark '_Fn{fid}' in footnote definition"

    def test_delete_footnote_removes_hyperlink_container(self):
        """delete_footnote removes the <w:hyperlink> wrapper when ref is hyperlink-wrapped."""
        W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

        result = _j(server.add_footnote("00000004", "To be deleted"))
        fid = result["footnote_id"]

        server.delete_footnote(fid)

        doc_obj = server._docs[server._DEFAULT_HANDLE]
        doc_tree = doc_obj._tree("word/document.xml")

        remaining = [
            el
            for el in doc_tree.iter(f"{{{W}}}hyperlink")
            if el.get(f"{{{W}}}anchor") == f"_Fn{fid}"
        ]
        assert remaining == [], "Hyperlink container must be removed on delete"


class TestFootnoteRef:
    @pytest.fixture(autouse=True)
    def _open(self, test_docx: Path):
        server.open_document(str(test_docx))

    def test_add_footnote_ref_creates_hyperlink_in_body(self):
        """add_footnote_ref inserts a hyperlink to the existing footnote's anchor."""
        W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

        add_result = _j(server.add_footnote("00000004", "Original footnote"))
        fid = add_result["footnote_id"]

        ref_result = _j(server.add_footnote_ref("00000003", fid))
        assert ref_result["footnote_id"] == fid

        doc_obj = server._docs[server._DEFAULT_HANDLE]
        doc_tree = doc_obj._tree("word/document.xml")

        hyperlinks = [
            el
            for el in doc_tree.iter(f"{{{W}}}hyperlink")
            if el.get(f"{{{W}}}anchor") == f"_Fn{fid}"
        ]
        # One from add_footnote (para 00000004), one from add_footnote_ref (para 00000003)
        assert len(hyperlinks) == 2, f"Expected 2 hyperlinks to _Fn{fid}, found {len(hyperlinks)}"

    def test_add_footnote_ref_not_found_raises(self):
        """add_footnote_ref raises ValueError for a non-existent footnote ID."""
        with pytest.raises(ValueError, match="not found"):
            server.add_footnote_ref("00000004", 999)

    def test_add_footnote_ref_does_not_create_new_definition(self):
        """add_footnote_ref does NOT add a new entry in footnotes.xml."""
        W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

        add_result = _j(server.add_footnote("00000004", "Original"))
        fid = add_result["footnote_id"]

        doc_obj = server._docs[server._DEFAULT_HANDLE]
        fn_tree = doc_obj._tree("word/footnotes.xml")
        count_before = len(fn_tree.findall(f"{{{W}}}footnote"))

        server.add_footnote_ref("00000003", fid)
        count_after = len(fn_tree.findall(f"{{{W}}}footnote"))

        assert count_after == count_before, (
            "add_footnote_ref must not create a new footnote definition"
        )

    def test_add_footnote_ref_comma_delimiter_with_existing_ref(self):
        """add_footnote_ref inserts comma when the target paragraph already ends with a ref."""
        W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        W14 = "http://schemas.microsoft.com/office/word/2010/wordml"

        add_result = _j(server.add_footnote("00000004", "Source A"))
        fid = add_result["footnote_id"]

        # Add a second footnote to para 00000003
        _j(server.add_footnote("00000003", "Source B"))
        # Now add a ref to fid on the same para 00000003 (should insert comma)
        _j(server.add_footnote_ref("00000003", fid))

        doc_obj = server._docs[server._DEFAULT_HANDLE]
        doc_tree = doc_obj._tree("word/document.xml")

        para = next(
            p for p in doc_tree.iter(f"{{{W}}}p") if p.get(f"{{{W14}}}paraId") == "00000003"
        )
        children = list(para)
        fn_elems = [
            c
            for c in children
            if (c.tag == f"{{{W}}}hyperlink" and list(c.iter(f"{{{W}}}footnoteReference")))
            or (c.tag == f"{{{W}}}r" and c.find(f"{{{W}}}footnoteReference") is not None)
        ]
        assert len(fn_elems) >= 2

        # Last two fn elements must be separated by a comma run
        idx1 = children.index(fn_elems[-2])
        idx2 = children.index(fn_elems[-1])
        assert idx2 == idx1 + 2
        comma_t = children[idx1 + 1].find(f"{{{W}}}t")
        assert comma_t is not None and comma_t.text == ","


# ═══════════════════════════════════════════════════════════════════════════
#  TestEndnoteCRUD
# ═══════════════════════════════════════════════════════════════════════════


class TestEndnoteCRUD:
    @pytest.fixture(autouse=True)
    def _open(self, test_docx: Path):
        server.open_document(str(test_docx))

    def test_update_endnote(self):
        """Update existing endnote #1 text and verify the result."""
        result = _j(server.update_endnote(1, "Updated endnote text."))
        assert result["endnote_id"] == 1
        assert result["text"] == "Updated endnote text."
        # Verify via get_endnotes
        endnotes = _j(server.get_endnotes())
        en1 = next(e for e in endnotes if e["id"] == 1)
        assert "Updated endnote text." in en1["text"]

    def test_update_endnote_not_found(self):
        """Updating a non-existent endnote raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            server.update_endnote(999, "Should fail")

    def test_update_endnote_builtin_rejected(self):
        """Updating built-in endnote (id < 1) raises ValueError."""
        with pytest.raises(ValueError):
            server.update_endnote(0, "Should fail")

    def test_delete_endnote_removes_from_xml(self):
        """delete_endnote removes the definition from endnotes.xml."""
        result = _j(server.delete_endnote(1))
        assert result["deleted"] == 1
        endnotes = _j(server.get_endnotes())
        ids = [e["id"] for e in endnotes]
        assert 1 not in ids

    def test_delete_endnote_removes_reference(self):
        """delete_endnote also removes the endnoteReference run in document.xml."""
        server.delete_endnote(1)
        # validate_endnotes should report valid (no dangling refs)
        validation = _j(server.validate_endnotes())
        assert validation["valid"] is True
        assert 1 not in validation.get("orphaned_refs", [])

    def test_delete_endnote_not_found(self):
        """Deleting a non-existent endnote raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            server.delete_endnote(999)

    def test_update_endnote_then_read_back(self):
        """Round-trip: add a new endnote, update it, confirm text changed."""
        add_result = _j(server.add_endnote("00000004", "Initial endnote"))
        eid = add_result["endnote_id"]
        _j(server.update_endnote(eid, "Revised endnote"))
        endnotes = _j(server.get_endnotes())
        en = next(e for e in endnotes if e["id"] == eid)
        assert "Revised endnote" in en["text"]


# ═══════════════════════════════════════════════════════════════════════════
#  TestFootnoteUrlHotlink
# ═══════════════════════════════════════════════════════════════════════════


class TestFootnoteUrlHotlink:
    @pytest.fixture(autouse=True)
    def _open(self, test_docx: Path):
        server.open_document(str(test_docx))

    def test_add_footnote_with_url_contains_hyperlink_element(self):
        """add_footnote with url= produces a <w:hyperlink> in the footnote body."""

        W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

        result = _j(server.add_footnote("00000004", "See reference", url="https://example.com"))
        fid = result["footnote_id"]

        doc_obj = server._docs[server._DEFAULT_HANDLE]
        fn_tree = doc_obj._tree("word/footnotes.xml")

        target_fn = None
        for fn in fn_tree.findall(f"{{{W}}}footnote"):
            if fn.get(f"{{{W}}}id") == str(fid):
                target_fn = fn
                break
        assert target_fn is not None, f"Footnote {fid} not found"

        # Must contain a w:hyperlink element
        hyperlinks = list(target_fn.iter(f"{{{W}}}hyperlink"))
        assert len(hyperlinks) == 1, "Expected exactly one <w:hyperlink>"

        hl = hyperlinks[0]
        r_id = hl.get(f"{{{R}}}id")
        assert r_id is not None and r_id.startswith("rId"), f"Hyperlink missing r:id, got {r_id!r}"

        # The hyperlink run must have Hyperlink rStyle and the URL as text
        runs = hl.findall(f"{{{W}}}r")
        assert runs, "Hyperlink must contain at least one <w:r>"
        url_text = "".join(t.text for t in hl.iter(f"{{{W}}}t") if t.text)
        assert "https://example.com" in url_text

        # Run must carry Hyperlink character style
        rpr = runs[0].find(f"{{{W}}}rPr")
        assert rpr is not None
        rs = rpr.find(f"{{{W}}}rStyle")
        assert rs is not None and rs.get(f"{{{W}}}val") == "Hyperlink"

    def test_add_footnote_with_url_creates_rels_entry(self):
        """add_footnote with url= registers an External relationship in footnotes.xml.rels."""
        RELS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
        URL = "https://example.com/source"

        result = _j(server.add_footnote("00000004", "Cited source", url=URL))
        _ = result["footnote_id"]

        doc_obj = server._docs[server._DEFAULT_HANDLE]
        rels = doc_obj._tree("word/_rels/footnotes.xml.rels")
        assert rels is not None, "word/_rels/footnotes.xml.rels was not created"

        relationships = rels.findall(f"{{{RELS_NS}}}Relationship")
        external_for_url = [
            r for r in relationships if r.get("Target") == URL and r.get("TargetMode") == "External"
        ]
        assert len(external_for_url) == 1, (
            f"Expected one External relationship for {URL!r}, found {len(external_for_url)}"
        )

    def test_add_footnote_without_url_no_hyperlink(self):
        """add_footnote without url= produces no <w:hyperlink> (backward-compat)."""

        W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

        result = _j(server.add_footnote("00000004", "Plain footnote text"))
        fid = result["footnote_id"]

        doc_obj = server._docs[server._DEFAULT_HANDLE]
        fn_tree = doc_obj._tree("word/footnotes.xml")

        target_fn = next(
            fn for fn in fn_tree.findall(f"{{{W}}}footnote") if fn.get(f"{{{W}}}id") == str(fid)
        )
        hyperlinks = list(target_fn.iter(f"{{{W}}}hyperlink"))
        assert hyperlinks == [], "No hyperlink expected when url is omitted"

    def test_add_footnote_two_urls_get_distinct_rids(self):
        """Two add_footnote calls with different URLs register distinct rId values."""
        RELS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
        W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

        server.add_footnote("00000004", "First", url="https://alpha.example.com")
        server.add_footnote("00000004", "Second", url="https://beta.example.com")

        doc_obj = server._docs[server._DEFAULT_HANDLE]
        fn_tree = doc_obj._tree("word/footnotes.xml")

        r_ids = []
        for fn in fn_tree.findall(f"{{{W}}}footnote"):
            for hl in fn.iter(f"{{{W}}}hyperlink"):
                rid = hl.get(f"{{{R}}}id")
                if rid:
                    r_ids.append(rid)

        assert len(r_ids) == 2, f"Expected 2 hyperlinks, got {len(r_ids)}"
        assert r_ids[0] != r_ids[1], "Both hyperlinks must have distinct rId values"

        rels = doc_obj._tree("word/_rels/footnotes.xml.rels")
        assert rels is not None
        rels_ns = RELS_NS
        targets = {r.get("Target") for r in rels.findall(f"{{{rels_ns}}}Relationship")}
        assert "https://alpha.example.com" in targets
        assert "https://beta.example.com" in targets

    def test_add_footnote_url_present_in_result(self):
        """Return dict from add_footnote includes url when provided."""
        result = _j(server.add_footnote("00000004", "Label", url="https://ref.example.com"))
        assert result.get("url") == "https://ref.example.com"
