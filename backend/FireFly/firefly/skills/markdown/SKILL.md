---
name: markdown
description: 文档格式处理技能，提供 PDF→Markdown 转换与本地 RAG 检索/建索引（rag_search、rag_index 工具）。
metadata: {"firefly":{"emoji":"📝"}}
version: 1.0.0
---

# Markdown 工具技能

本技能提供文档处理脚本，当前包含：

- `scripts/pdf_to_markdown.py`：将 `llm-wiki/raw/pdf` 批量转换到 `llm-wiki/raw/markdown`
- `scripts/markdown_to_rag.py`：将 `llm-wiki/raw/markdown` 批量切片到 `llm-wiki/raw/rag`（保持目录镜像；**按 Markdown 标题分章节**，章节内受 `--max-chars` 约束；**参考文献 / References 整节合并为单条 chunk**，`chunk_kind` 为 `references`）

## Agent 工具（优先）

文献问答时由 agent **自主调用**，不再默认注入 `[RAG Context]`：

- **`rag_search`**：`query` + `wiki_pdf_path`（来自 `[zotero_current_wiki_pdf_path=…]`）/ `rag_path` / `markdown_path`；可选 `ensure_index=true` 在缺索引时先切片
- **`rag_index`**：从 `markdown_path` 或 `wiki_pdf_path` 构建/刷新 `raw/rag/*.jsonl`
- **`get_markdown_headings`**：一次返回全文标题树与 **行号**（替代多次 grep 定位章节）

脚本仍可用于批量离线建库；对话内检索优先用上述工具。

## RAG 问答规范

- 以 `rag_search` 结果为**主要事实来源**；勿用通用知识替代检索未覆盖的细节。
- **总结、方法提取、算法综述**：先 1–3 次有针对性的 `rag_search`（不同 query 覆盖各小节），再按需 `read_file` 扩展公式；**禁止**用多次 `grep` 扫全文代替 RAG。
- 索引缺失时先 `rag_index`，或 `rag_search(ensure_index=true)`。
- **勿向用户暴露检索 mechanics**：避免「从 chunk 1 可知」「according to fragment N」等表述；需要溯源时用章节/主题，不用 chunk 编号。

## 在长 markdown 中定位章节

`raw/markdown/*.md` 的行号与论文章节号、PDF 页码**不对应**。

### 总结 / 方法类任务（默认）

1. **`rag_search`**：`markdown_path` + 主题 query（如「联合干扰感知方法」「3.1 双向双滑窗」「ISRJ 重构公式」）；缺索引则 `ensure_index=true`。
2. 若需完整公式链或步骤细节，根据 RAG 返回的 **`start_line=`** 或章节提示，**单次** `read_file(path, offset=…, limit=…)` 扩展。
3. 若 RAG 无行号且需全文结构，调用 **`get_markdown_headings(path)` 一次**，再用返回的 `line` 做 `read_file` — **禁止**连续多轮 `grep`。

**禁止**：连续多轮 `grep`（「方法|算法|公式|提出|本文…」）试探；禁止从 `offset=1` 顺序 read 找章节。

### 精确锚点（非总结类）

若问题只是「某词出现在哪一行」：

```text
grep(pattern="仿真实验", path=<md>, output_mode="content")
read_file(path, offset=<grep 行号>, limit=400)
```

常用示例：

```bash
# 批量 markdown -> rag（默认根目录）
python firefly/skills/markdown/scripts/markdown_to_rag.py

# 单文件 markdown -> rag
python firefly/skills/markdown/scripts/markdown_to_rag.py --markdown backend/llm-wiki/raw/markdown/抗干扰/频率捷变/FDIXGRX8_脉间-脉内捷变频雷达抗间歇采样干扰方法_刘智星.md --rag backend/llm-wiki/raw/rag/抗干扰/频率捷变/FDIXGRX8_脉间-脉内捷变频雷达抗间歇采样干扰方法_刘智星.jsonl

# 用户要求“展示 chunks”时可加预览输出
python firefly/skills/markdown/scripts/markdown_to_rag.py --markdown backend/llm-wiki/raw/markdown/抗干扰/频率捷变/FDIXGRX8_脉间-脉内捷变频雷达抗间歇采样干扰方法_刘智星.md --show-chunks --show-limit 8
```

默认行为：

- 保持目录结构一致
- 默认不覆盖已存在 Markdown
- 失败文件记录在输出结果中
- 图片引用与其 OCR 文本块按原子单元切片，避免被切碎
- RAG 切片：按 ATX/setext 标题建 `section_path`；参考文献类标题单独成一块，便于引用检索
