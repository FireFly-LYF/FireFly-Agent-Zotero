---
name: markdown
description: 文档格式处理技能，提供 PDF 到 Markdown 的批量转换脚本。
metadata: {"nanobot":{"emoji":"📝"}}
version: 1.0.0
---

# Markdown 工具技能

本技能提供文档处理脚本，当前包含：

- `scripts/pdf_to_markdown.py`：将 `llm-wiki/raw/pdf` 批量转换到 `llm-wiki/raw/markdown`
- `scripts/markdown_to_rag.py`：将 `llm-wiki/raw/markdown` 批量切片到 `llm-wiki/raw/rag`（保持目录镜像）

默认行为：

- 保持目录结构一致
- 默认不覆盖已存在 Markdown
- 失败文件记录在输出结果中
- 图片引用与其 OCR 文本块按原子单元切片，避免被切碎
