#!/usr/bin/env python3
"""
RAG 召回工具函数。

提供章节感知的 RAG 召回策略，支持：
- 章节名优先匹配（优先匹配章节标题文本，而非仅章节号）
- 层级扩展（匹配到子章节时召回整个父章节）
- 数字章节和文本章节
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# 数字章节标题正则（如 "4.3 仿真结果"）
_RAG_NUM_HEADING_START_RE = re.compile(r"^(\d+)(\.\d+)*(\s|$)")


def _strip_heading_markdown_noise(segment: str) -> str:
    """去掉标题里的 Markdown 加粗等，便于解析 **4.2** 这类期刊标题。"""
    s = re.sub(r"\*+", "", segment)
    return re.sub(r"\s+", " ", s).strip()


def segment_leading_numeric_tuple(segment: str) -> tuple[int, ...] | None:
    """
    从路径片段解析前导章节号：**4.2** 干扰有效性 -> (4, 2)；无编号则 None。
    """
    s = _strip_heading_markdown_noise(segment)
    if not s:
        return None
    m = re.match(r"^(\d+(?:\.\d+)*)", s)
    if not m:
        return None
    try:
        return tuple(int(x) for x in m.group(1).split("."))
    except ValueError:
        return None


def extract_query_numeric_prefixes(query: str) -> list[tuple[int, ...]]:
    """从用户问题中提取章节号意图，如 4.2.1、4.2、单独 4（长者优先）。"""
    out: list[tuple[int, ...]] = []
    # 勿匹配标识符内的数字（如 unique_h1 中的 1）
    for m in re.finditer(r"(?<![A-Za-z0-9_])\d+(?:\.\d+)*(?![A-Za-z0-9_])", query or ""):
        raw = m.group(0)
        # 减少「2024」等年份被当成章节号
        if re.fullmatch(r"20\d{2}|19\d{2}", raw):
            continue
        try:
            out.append(tuple(int(x) for x in raw.split(".")))
        except ValueError:
            continue
    out.sort(key=lambda t: (-len(t), t))
    seen: set[tuple[int, ...]] = set()
    uniq: list[tuple[int, ...]] = []
    for t in out:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq


def numeric_section_boost_score(
    seg_tuple: tuple[int, ...],
    query_tuple: tuple[int, ...],
) -> int:
    """
    seg 与查询章节号的匹配强度。
    - seg 与 query 前缀一致且 seg 更深或同级：最高（用户问 4.2，命中 4.2 / 4.2.1）
    - seg 为 query 的真前缀：中等（用户问 4.2.1，命中 4.2 父节）
    """
    if not seg_tuple or not query_tuple:
        return 0
    # 命中小节或其子节
    if len(seg_tuple) >= len(query_tuple) and seg_tuple[: len(query_tuple)] == query_tuple:
        return 500_000 + 10_000 * len(query_tuple) + 100 * len(seg_tuple)
    # 仅命中父级编号
    if len(seg_tuple) < len(query_tuple) and query_tuple[: len(seg_tuple)] == seg_tuple:
        return 50_000 + 1_000 * len(seg_tuple)
    return 0


def _best_numeric_boost_for_paths(section_path: str, parent_path: str, query_nums: list[tuple[int, ...]]) -> int:
    """对 section_path / parent_section_path 各段计算编号匹配最高分。"""
    if not query_nums:
        return 0
    paths = [section_path, parent_path]
    best = 0
    for p in paths:
        if not str(p).strip():
            continue
        for seg in [s.strip() for s in str(p).split(">") if s.strip()]:
            st = segment_leading_numeric_tuple(seg)
            if not st:
                continue
            for qn in query_nums:
                best = max(best, numeric_section_boost_score(st, qn))
    return best


def title_heading_overlap_boost(segment: str, query_raw: str) -> int:
    """去掉编号后的章节标题若完整出现在用户问题中，给予强加成（如「干扰有效性验证」）。"""
    if not segment or not query_raw:
        return 0
    cleaned = _strip_heading_markdown_noise(segment)
    m = re.match(r"^\d+(?:\.\d+)*\s*(.+)$", cleaned)
    title_rest = m.group(1).strip() if m else cleaned
    if len(title_rest) < 3:
        return 0
    if title_rest in query_raw:
        return 80_000
    return 0


def _best_title_overlap_for_paths(section_path: str, query_raw: str) -> int:
    if not section_path or not str(query_raw).strip():
        return 0
    best = 0
    for seg in [s.strip() for s in section_path.split(">") if s.strip()]:
        best = max(best, title_heading_overlap_boost(seg, query_raw.strip()))
    return best


def _token_matches_chapter_num(tok: str, chapter_num: str) -> bool:
    """避免「1」误命中「4.1」这类子串匹配。"""
    cn = chapter_num.lower().strip()
    t = (tok or "").lower().strip()
    if not t or not cn:
        return False
    if t == cn:
        return True
    if cn.startswith(t + "."):
        return True
    return False


def tokenize_rag_query(text: str) -> list[str]:
    """
    中英文混合粗分词。
    
    改进：支持章节号匹配（如 "4.2"）
    
    Args:
        text: 查询文本
        
    Returns:
        分词后的 token 列表（小写）
    
    Examples:
        "4.2节的仿真" -> ["4.2", "节的仿真"]
        "Introduction" -> ["introduction"]
    """
    # 匹配：中文片段 | 数字（含 4.2） | 英文标识符（含 unique_h1，避免拆成 1、2）
    return [
        tok.lower()
        for tok in re.findall(
            r"[\u4e00-\u9fff]+|\d+(?:\.\d+)*|[A-Za-z_][A-Za-z0-9_]*",
            text or "",
        )
        if tok
    ]


def rag_numeric_chapter_root(section_path: str) -> str | None:
    """
    从 section_path 最后一段取最外层章号。
    
    Args:
        section_path: 章节路径，如 "4 > 4.2 > 4.2.1 仿真"
        
    Returns:
        根章节号，如 "4"；若无数字章节则返回 None
    """
    if not section_path or not str(section_path).strip():
        return None
    last = str(section_path).split(">")[-1].strip()
    tup = segment_leading_numeric_tuple(last)
    if tup:
        return str(tup[0])
    m = _RAG_NUM_HEADING_START_RE.match(last)
    return m.group(1) if m else None


def rag_chunk_in_numeric_chapter(section_path: str, chapter_root: str) -> bool:
    """
    判断 section_path 是否属于指定数字章节。
    
    Args:
        section_path: 章节路径
        chapter_root: 根章节号，如 "4"
        
    Returns:
        是否属于该章节
    """
    if not section_path or not chapter_root:
        return False
    for seg in str(section_path).split(">"):
        s = seg.strip()
        tup = segment_leading_numeric_tuple(s)
        if tup and str(tup[0]) == chapter_root:
            return True
        m = _RAG_NUM_HEADING_START_RE.match(s)
        if m and m.group(1) == chapter_root:
            return True
    return False


def section_belongs_to_chapter(section_path: str, chapter_prefix: str) -> bool:
    """
    判断 section_path 是否属于指定章节。
    支持数字章节（如 "4.3" 属于 "4"）和文本章节（精确匹配第一级）。
    
    Args:
        section_path: 章节路径，如 "4 > 4.3 > 4.3.1"
        chapter_prefix: 章节前缀，如 "4" 或 "Introduction"
        
    Returns:
        是否属于该章节
    """
    if not section_path or not chapter_prefix:
        return False
    
    segments = [seg.strip() for seg in section_path.split(">") if seg.strip()]
    if not segments:
        return False
    
    # 尝试数字章节匹配
    for seg in segments:
        tup = segment_leading_numeric_tuple(seg)
        if tup and str(tup[0]) == chapter_prefix:
            return True
        m = _RAG_NUM_HEADING_START_RE.match(seg)
        if m and m.group(1) == chapter_prefix:
            return True
    
    # 尝试文本章节匹配（第一级精确匹配）
    if segments[0] == chapter_prefix:
        return True
    
    return False


def retrieve_rag_chunks_with_chapter_expansion(
    query: str,
    rag_jsonl_path: Path,
    *,
    top_k: int = 20,
    min_hits_same_chapter: int = 1,
    max_chunks_per_expanded_chapter: int = 120,
) -> list[dict[str, Any]]:
    """
    章节感知的 RAG 召回策略。
    
    召回流程：
    1. 优先匹配完整章节名（如 "4.3 仿真结果"），章节标题文本权重 > 章节号权重
    2. 若匹配到子章节（如 "4.3"），则召回整个父章节（"4"）
    3. 对正文内容进行粗分词打分召回
    4. 若同一章在 top 命中中 >=min_hits，则并入该章全部 chunk（有上限）
    
    Args:
        query: 用户查询
        rag_jsonl_path: RAG JSONL 文件路径
        top_k: 初始召回的 top-k chunks（默认20，增加以提高召回率）
        min_hits_same_chapter: 同一章节最少命中次数才扩展（默认1，降低以更容易触发扩展）
        max_chunks_per_expanded_chapter: 每章最多召回的 chunks 数量
        
    Returns:
        召回的 chunk 记录列表，按 chunk_index 排序
    """
    if not rag_jsonl_path.is_file():
        return []

    query_nums = extract_query_numeric_prefixes(query or "")
    tokens = tokenize_rag_query(query or "")
    if not tokens and not query_nums:
        return []

    try:
        lines = rag_jsonl_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return []

    records: list[dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if not str(rec.get("text", "") or "").strip():
            continue
        records.append(rec)

    # ========== 第一阶段：章节名匹配（优先级最高） ==========
    section_matched: list[tuple[int, dict[str, Any]]] = []
    for rec in records:
        section_path = str(rec.get("section_path", "") or "")
        parent_path = str(rec.get("parent_section_path", "") or "")
        if not section_path:
            continue
        
        # 解析章节路径的各个层级（如 "4 > 4.3 > 4.3.1 仿真结果"）
        segments = [seg.strip() for seg in section_path.split(">") if seg.strip()]
        if not segments:
            continue

        nb = _best_numeric_boost_for_paths(section_path, parent_path, query_nums)
        tb = _best_title_overlap_for_paths(section_path, query or "")
        
        # 对每个层级的章节名进行匹配，优先匹配最深层（最具体的章节名）
        token_segment_best = 0
        for depth_idx, segment in enumerate(segments):
            cleaned = _strip_heading_markdown_noise(segment)
            segment_lower = cleaned.lower()
            
            # 分离章节号和章节名（支持 **4.3** 仿真结果）
            m_num = re.match(r"^(\d+(?:\.\d+)*)", cleaned)
            if m_num:
                chapter_num = m_num.group(1).strip()
                chapter_title = cleaned[m_num.end() :].strip()
                
                num_score = sum(1 for tok in tokens if _token_matches_chapter_num(tok, chapter_num))
                title_score = sum(1 for tok in tokens if tok in chapter_title.lower()) if chapter_title else 0
                
                segment_score = title_score * 3 + num_score
            else:
                segment_score = sum(1 for tok in tokens if tok in segment_lower)
            
            depth_weight = len(segments) - depth_idx
            weighted_score = segment_score * depth_weight
            
            if weighted_score > token_segment_best:
                token_segment_best = weighted_score
        
        combined = nb + tb + token_segment_best * 100
        if combined > 0:
            section_matched.append((combined, rec))

    # ========== 第二阶段：正文内容匹配 ==========
    content_scored: list[tuple[int, dict[str, Any]]] = []
    for rec in records:
        text = str(rec.get("text", "") or "")
        lowered = text.lower()
        score = sum(1 for tok in tokens if tok in lowered)
        if score > 0:
            content_scored.append((score, rec))

    # 合并两阶段结果（章节匹配优先）
    all_scored = section_matched + content_scored
    all_scored.sort(key=lambda x: x[0], reverse=True)
    
    # 去重（同一 chunk_index 只保留一次）
    seen_idx: set[int] = set()
    hits: list[dict[str, Any]] = []
    for _, rec in all_scored:
        try:
            idx = int(rec.get("chunk_index", -1))
        except (TypeError, ValueError):
            continue
        if idx >= 0 and idx not in seen_idx:
            seen_idx.add(idx)
            hits.append(rec)
            if len(hits) >= top_k:
                break

    # 两阶段均无命中时：整句中文 token 往往无法在正文整段出现，避免零召回
    if not hits and records:
        def _rec_idx(r: dict[str, Any]) -> int:
            try:
                return int(r.get("chunk_index", -1))
            except (TypeError, ValueError):
                return -1

        ordered = sorted((r for r in records if _rec_idx(r) >= 0), key=_rec_idx)
        hits = ordered[:top_k]

    # ========== 第三阶段：章节层级扩展 ==========
    # 提取所有命中的章节路径，并找出需要扩展的父章节
    expand_section_prefixes: set[str] = set()
    
    for rec in hits:
        section_path = str(rec.get("section_path", "") or "")
        if not section_path:
            continue
        
        # 解析章节路径（如 "4 > 4.3 > 4.3.1"）
        segments = [seg.strip() for seg in section_path.split(">") if seg.strip()]
        
        # 对于数字章节，提取根章节号（如 "4.3.1" -> "4"）
        for seg in segments:
            root = rag_numeric_chapter_root(seg)
            if root:
                expand_section_prefixes.add(root)
                break  # 只取最外层数字章节
        
        # 路径里已有编号小节时，不把第一级当作「文本章」（避免 D > 4.1 把 D 当成整篇扩展前缀）
        has_numeric_segment = any(segment_leading_numeric_tuple(s) for s in segments)
        if segments and not has_numeric_segment and not rag_numeric_chapter_root(segments[0]):
            expand_section_prefixes.add(segments[0])

    # 统计每个章节前缀的命中次数
    prefix_hit_count: dict[str, int] = {}
    for rec in hits:
        section_path = str(rec.get("section_path", "") or "")
        for prefix in expand_section_prefixes:
            if section_belongs_to_chapter(section_path, prefix):
                prefix_hit_count[prefix] = prefix_hit_count.get(prefix, 0) + 1

    # 确定需要扩展的章节（命中次数 >= min_hits）
    expand_chapters = {p for p, c in prefix_hit_count.items() if c >= min_hits_same_chapter}

    # ========== 第四阶段：收集最终结果 ==========
    by_index: dict[int, dict[str, Any]] = {}
    for rec in records:
        try:
            idx = int(rec.get("chunk_index", -1))
        except (TypeError, ValueError):
            continue
        if idx >= 0:
            by_index[idx] = rec

    include_idx: set[int] = set()
    
    # 先加入初始命中的 chunks
    for rec in hits:
        try:
            hi = int(rec.get("chunk_index", -1))
        except (TypeError, ValueError):
            continue
        if hi >= 0:
            include_idx.add(hi)

    # 扩展章节：加入整章的所有 chunks
    for chapter_prefix in expand_chapters:
        chapter_recs = [
            r for r in records
            if section_belongs_to_chapter(str(r.get("section_path", "") or ""), chapter_prefix)
        ]
        for r in chapter_recs[:max_chunks_per_expanded_chapter]:
            try:
                idx = int(r.get("chunk_index", -1))
            except (TypeError, ValueError):
                continue
            if idx >= 0:
                include_idx.add(idx)

    # 收集所有要返回的 chunks
    result_chunks = [by_index[i] for i in include_idx if i in by_index]
    
    # ========== 第五阶段：按相关性重排序 ==========
    def _calculate_relevance_score(rec: dict[str, Any]) -> tuple[int, int, int]:
        """
        计算每个 chunk 的相关性分数，用于排序。
        
        返回: (章节匹配分数, 内容匹配分数, chunk_index)
        - 章节匹配分数高的排在前面
        - 相同章节匹配分数时，内容匹配分数高的排在前面
        - 都相同时，按 chunk_index 排序（保持文档顺序）
        """
        section_path = str(rec.get("section_path", "") or "")
        parent_path = str(rec.get("parent_section_path", "") or "")
        text = str(rec.get("text", "") or "")
        
        nb = _best_numeric_boost_for_paths(section_path, parent_path, query_nums)
        tb = _best_title_overlap_for_paths(section_path, query or "")
        section_score = nb + tb
        token_best = 0
        if section_path:
            segments = [seg.strip() for seg in section_path.split(">") if seg.strip()]
            for depth_idx, segment in enumerate(segments):
                cleaned = _strip_heading_markdown_noise(segment)
                segment_lower = cleaned.lower()
                
                m_num = re.match(r"^(\d+(?:\.\d+)*)", cleaned)
                if m_num:
                    chapter_num = m_num.group(1).strip()
                    chapter_title = cleaned[m_num.end() :].strip()
                    
                    num_score = sum(1 for tok in tokens if _token_matches_chapter_num(tok, chapter_num))
                    title_score = sum(1 for tok in tokens if tok in chapter_title.lower()) if chapter_title else 0
                    
                    segment_score = title_score * 3 + num_score
                else:
                    segment_score = sum(1 for tok in tokens if tok in segment_lower)
                
                depth_weight = len(segments) - depth_idx
                weighted_score = segment_score * depth_weight
                
                if weighted_score > token_best:
                    token_best = weighted_score
        section_score += token_best
        
        content_score = sum(1 for tok in tokens if tok in text.lower())
        
        # 获取 chunk_index（用于相同分数时保持文档顺序）
        try:
            chunk_idx = int(rec.get("chunk_index", 0))
        except (TypeError, ValueError):
            chunk_idx = 0
        
        # 返回排序键：章节分数（降序）、内容分数（降序）、chunk_index（升序）
        return (-section_score, -content_score, chunk_idx)
    
    # 按相关性排序
    result_chunks.sort(key=_calculate_relevance_score)
    
    return result_chunks
