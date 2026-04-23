---
name: wiki
description: 将 llm-wiki 的 PDF 批量转换为 Markdown，并保持与 PDF 目录一致。
metadata: {"firefly":{"emoji":"🧾"}}
version: 1.0.0
---

# Wiki PDF/Markdown 处理

当用户提出“把 `llm-wiki/raw/pdf` 转成 markdown”“批量转 PDF”“把 markdown 切片成 rag”这类请求时，优先使用本技能。

## 目标

- 使用 `pymupdf4llm` 将 PDF 转为 Markdown
- 默认输入目录：`backend/llm-wiki/raw/pdf`
- 默认输出目录：`backend/llm-wiki/raw/markdown`
- 输出目录结构必须与 PDF 目录结构一致
- LLM 不负责 PDF 放入，只处理已存在 PDF
- 支持将 `raw/markdown` 切片为 `raw/rag`（用于检索）

## 脚本入口

提供三种模式：

1. 固定目录批量转换
   - 脚本：`firefly/skills/markdown/scripts/pdf_to_markdown.py`
   - 用于 `raw/pdf` -> `raw/markdown` 的批处理
2. 指定路径单文件转换
   - 脚本：`firefly/skills/markdown/scripts/pdf_to_markdown.py`
   - 用于“某目录下某个 PDF -> 某目录下某个 Markdown”
3. Markdown 转 RAG 切片
   - 脚本：`firefly/skills/markdown/scripts/markdown_to_rag.py`
   - 用于 `raw/markdown` -> `raw/rag`（目录镜像）

常用示例：

```bash
# 固定目录批量转换
python firefly/skills/markdown/scripts/pdf_to_markdown.py
python firefly/skills/markdown/scripts/pdf_to_markdown.py --overwrite
python firefly/skills/markdown/scripts/pdf_to_markdown.py --pdf-root backend/llm-wiki/raw/pdf --markdown-root backend/llm-wiki/raw/markdown

# 指定路径单文件转换（同一脚本）
python firefly/skills/markdown/scripts/pdf_to_markdown.py --pdf backend/llm-wiki/raw/pdf/抗干扰/demo.pdf --markdown backend/llm-wiki/raw/markdown/抗干扰/demo.md
python firefly/skills/markdown/scripts/pdf_to_markdown.py --pdf D:/data/a.pdf --markdown D:/out/a.md --overwrite

# markdown -> rag（批量）
python firefly/skills/markdown/scripts/markdown_to_rag.py
python firefly/skills/markdown/scripts/markdown_to_rag.py --markdown-root backend/llm-wiki/raw/markdown --rag-root backend/llm-wiki/raw/rag

# markdown -> rag（单文件）
python firefly/skills/markdown/scripts/markdown_to_rag.py --markdown backend/llm-wiki/raw/markdown/抗干扰/demo.md --rag backend/llm-wiki/raw/rag/抗干扰/demo.jsonl
python firefly/skills/markdown/scripts/markdown_to_rag.py --markdown backend/llm-wiki/raw/markdown/抗干扰/demo.md --show-chunks --show-limit 8
```

## 行为约束

- 不修改 `raw/pdf` 中任何文件
- 默认不覆盖已存在 `.md` 文件（可用 `--overwrite`）
- 仅将扩展名为 `.pdf` 的文件转换为同名 `.md`
- 遇到单文件失败时继续处理其他文件，并汇总失败列表
- 单文件模式只处理指定输入与指定输出，不改变固定目录批量逻辑
- 每个 Markdown 使用独立图片目录：`<markdown_stem>.assets/`，避免多个文档共用同一 `images/` 目录
- 若用户未指定路径并采用默认路径，必须在回复中显式告知默认输入/输出目录，再执行命令

## 输出说明

脚本执行后会打印 JSON 结果，包含：

- `pdf_root`
- `markdown_root`
- `converted`
- `skipped`
- `failed`

单文件模式返回：

- `pdf_path`
- `markdown_path`
- `status`（`converted` 或 `skipped`）

RAG 切片模式返回：

- `mode`（`batch` 或 `single`）
- `converted` / `result`
- `failed`（批量模式）
- `chunk_preview`（启用 `--show-chunks` 时）
