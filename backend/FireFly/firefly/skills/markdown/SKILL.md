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

脚本仍可用于批量离线建库；对话内检索优先用上述工具。

## 在长 markdown 中定位章节

`raw/markdown/*.md` 的行号与论文章节号、PDF 页码**不对应**。不要用 `read_file` 的 `offset` 凭感觉跳读（例如 `offset=100` 并不等于第四章）。

推荐流程：

1. **`grep`**：`path` 设为该 `.md`，`output_mode=content`，用标题或关键词（如 `仿真实验`、`## **4**`、`实验步骤`）。
2. 根据 grep 输出的**行号**，再 `read_file(path, offset=行号, limit=…)` 精读该段。
3. 若问题可由 RAG 覆盖，优先 **`rag_search`**，减少手工翻页。

```text
# 错误：猜 offset
read_file(path, offset=200, limit=500)

# 正确：先定位再读
grep(pattern="仿真实验", path=path, output_mode="content")
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
