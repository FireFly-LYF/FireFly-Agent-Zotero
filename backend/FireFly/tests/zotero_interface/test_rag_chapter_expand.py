"""本地 RAG：同一数字章多命中时整章扩展；编号/加粗标题召回排序。"""
from __future__ import annotations

import json

from firefly.skills.markdown.scripts.rag_utils import (
    extract_query_numeric_prefixes,
    rag_chunk_in_numeric_chapter,
    rag_numeric_chapter_root,
    retrieve_rag_chunks_with_chapter_expansion,
)


def test_rag_numeric_chapter_root():
    assert rag_numeric_chapter_root("Doc > 4.2 仿真设置") == "4"
    assert rag_numeric_chapter_root("Introduction") is None


def test_rag_chunk_in_numeric_chapter():
    assert rag_chunk_in_numeric_chapter("P > 4.1 a > 4.2 b", "4")
    assert not rag_chunk_in_numeric_chapter("P > 3.1 节", "4")
    # 10.1 的章根是 10，不是 1
    assert not rag_chunk_in_numeric_chapter("P > 10.1", "1")


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
    out = retrieve_rag_chunks_with_chapter_expansion("unique_h1 unique_h2", p, top_k=6)
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
    out = retrieve_rag_chunks_with_chapter_expansion(
        "solo_hit_word",
        p,
        top_k=6,
        min_hits_same_chapter=2,
    )
    assert [r["chunk_index"] for r in out] == [0]


def test_extract_numeric_prefix_includes_single_section_number():
    assert (4,) in extract_query_numeric_prefixes("请看第4章的方法")
    assert (4, 2) in extract_query_numeric_prefixes("第4.2节的结果")
    assert not extract_query_numeric_prefixes("发表于2024年的综述")


def test_fallback_when_no_token_overlap(tmp_path):
    """整句与正文无公共子串时仍返回篇首若干块，避免零召回。"""
    recs = [
        {"chunk_index": 0, "section_path": "D > A", "text": "alpha"},
        {"chunk_index": 1, "section_path": "D > B", "text": "beta"},
    ]
    p = tmp_path / "rag.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in recs), encoding="utf-8")
    out = retrieve_rag_chunks_with_chapter_expansion(
        "这段和用户问题完全不像的一段随机描述xyzqw",
        p,
        top_k=2,
    )
    assert len(out) == 2
    assert [r["chunk_index"] for r in out] == [0, 1]


def test_query_4_2_bold_path_ranks_target_section(tmp_path):
    """用户问 4.2 与「干扰有效性验证」时，应优先 **4.2** 节而非篇首/第 1 章。"""
    recs = [
        {
            "chunk_index": 0,
            "section_path": "Doc > **1** 强化学习理论简述",
            "text": "强化学习 智能体 环境 奖励 摘要 关键词",
        },
        {
            "chunk_index": 1,
            "section_path": "Doc > **4** 实验仿真和结果分析 > **4.2** 干扰有效性验证",
            "text": "本节给出干扰有效性验证的仿真 setup 与曲线 unique_section_42_body",
        },
        {
            "chunk_index": 2,
            "section_path": "Doc > **4** 实验仿真和结果分析 > **4.1** 干扰决策仿真",
            "text": "决策仿真内容",
        },
    ]
    p = tmp_path / "rag.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in recs), encoding="utf-8")
    out = retrieve_rag_chunks_with_chapter_expansion(
        "分析4.2节的干扰有效性验证",
        p,
        top_k=5,
    )
    assert out[0]["chunk_index"] == 1
    assert "**4.2**" in out[0]["section_path"] or "4.2" in out[0]["section_path"]
