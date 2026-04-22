---
name: wiki
description: 将 llm-wiki 的 PDF 批量转换为 Markdown，并保持与 PDF 目录一致。
metadata: {"nanobot":{"emoji":"🧾"}}
version: 1.0.0
---

# Wiki PDF 转 Markdown

当用户提出“把 `llm-wiki/raw/pdf` 转成 markdown”“批量转 PDF”这类请求时，优先使用本技能。

## 目标

- 使用 `pymupdf4llm` 将 PDF 转为 Markdown
- 默认输入目录：`backend/llm-wiki/raw/pdf`
- 默认输出目录：`backend/llm-wiki/raw/markdown`
- 输出目录结构必须与 PDF 目录结构一致
- LLM 不负责 PDF 放入，只处理已存在 PDF

## 脚本入口

提供两种模式：

1. 固定目录批量转换
   - 脚本：`nanobot/skills/markdown/scripts/pdf_to_markdown.py`
   - 用于 `raw/pdf` -> `raw/markdown` 的批处理
2. 指定路径单文件转换
   - 脚本：`nanobot/skills/markdown/scripts/single_pdf_to_markdown.py`
   - 用于“某目录下某个 PDF -> 某目录下某个 Markdown”

常用示例：

```bash
# 固定目录批量转换
python nanobot/skills/markdown/scripts/pdf_to_markdown.py
python nanobot/skills/markdown/scripts/pdf_to_markdown.py --overwrite
python nanobot/skills/markdown/scripts/pdf_to_markdown.py --pdf-root backend/llm-wiki/raw/pdf --markdown-root backend/llm-wiki/raw/markdown

# 指定路径单文件转换
python nanobot/skills/markdown/scripts/single_pdf_to_markdown.py --pdf backend/llm-wiki/raw/pdf/抗干扰/demo.pdf --markdown backend/llm-wiki/raw/markdown/抗干扰/demo.md
python nanobot/skills/markdown/scripts/single_pdf_to_markdown.py --pdf D:/data/a.pdf --markdown D:/out/a.md --overwrite
```

## 行为约束

- 不修改 `raw/pdf` 中任何文件
- 默认不覆盖已存在 `.md` 文件（可用 `--overwrite`）
- 仅将扩展名为 `.pdf` 的文件转换为同名 `.md`
- 遇到单文件失败时继续处理其他文件，并汇总失败列表
- 单文件模式只处理指定输入与指定输出，不改变固定目录批量逻辑
- 每个 Markdown 使用独立图片目录：`<markdown_stem>.assets/`，避免多个文档共用同一 `images/` 目录

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
