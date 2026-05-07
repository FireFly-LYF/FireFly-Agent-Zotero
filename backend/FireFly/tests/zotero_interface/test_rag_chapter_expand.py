"""本地 RAG：同一数字章多命中时整章扩展。"""
from __future__ import annotations

import json

from firefly.zotero_interface.commands import (
    _rag_chunk_in_numeric_chapter,
    _rag_numeric_chapter_root,
    _retrieve_local_rag_chunks_expand_chapters,
)


def test_rag_numeric_chapter_root():
    assert _rag_numeric_chapter_root("Doc > 4.2 仿真设置") == "4"
    assert _rag_numeric_chapter_root("Introduction") is None


def test_rag_chunk_in_numeric_chapter():
    assert _rag_chunk_in_numeric_chapter("P > 4.1 a > 4.2 b", "4")
    assert not _rag_chunk_in_numeric_chapter("P > 3.1 节", "4")
    # 10.1 的章根是 10，不是 1
    assert not _rag_chunk_in_numeric_chapter("P > 10.1", "1")


def test_expand_chapter_when_two_hits_same_chapter(tmp_path):
    recs = [
        {"chunk_index": 0, "section_path": "D > 1 Intro", "text": "intro only"},
        {"chunk_index": 1, "section_path": "D > 4.1 A", "text": "alpha unique_h1"},
        {"chunk_index": 2, "section_path": "D > 4.2 B", "text": "beta unique_h2"},
        {"chunk_index": 3, "section_path": "D > 4.3 C", "text": "gamma only_in_43"},
        {"chunk_index": 4, "section_path": "D > 5 Methods", "text": "methods end"},
    ]
    p = tmp_path / "rag.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in recs), encoding="utf-8")
    out = _retrieve_local_rag_chunks_expand_chapters("unique_h1 unique_h2", p, top_k=6)
    idxs = [r["chunk_index"] for r in out]
    assert idxs == [1, 2, 3]
    assert 0 not in idxs and 4 not in idxs


def test_no_expand_single_hit_in_chapter(tmp_path):
    recs = [
        {"chunk_index": 0, "section_path": "D > 4.1 A", "text": "solo_hit_word"},
        {"chunk_index": 1, "section_path": "D > 4.2 B", "text": "other"},
    ]
    p = tmp_path / "rag.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in recs), encoding="utf-8")
    out = _retrieve_local_rag_chunks_expand_chapters("solo_hit_word", p, top_k=6)
    assert [r["chunk_index"] for r in out] == [0]
