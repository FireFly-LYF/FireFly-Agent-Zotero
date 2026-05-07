---
name: markdown
description: 文档格式处理技能，提供 PDF 到 Markdown 的批量转换脚本。
metadata: {"firefly":{"emoji":"📝"}}
version: 1.0.0
---

# Markdown 工具技能

本技能提供文档处理脚本，当前包含：

- `scripts/pdf_to_markdown.py`：将 `llm-wiki/raw/pdf` 批量转换到 `llm-wiki/raw/markdown`
- `scripts/markdown_to_rag.py`：将 `llm-wiki/raw/markdown` 批量切片到 `llm-wiki/raw/rag`（保持目录镜像；**按 Markdown 标题分章节**，章节内受 `--max-chars` 约束；**参考文献 / References 整节合并为单条 chunk**，`chunk_kind` 为 `references`）

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
