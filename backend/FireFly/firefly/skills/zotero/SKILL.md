---
name: zotero
description: 在 firefly zotero 场景下读取 Zotero 文献元数据、笔记、批注与附件文本。
metadata: {"firefly":{"emoji":"📚"}}
---

# Zotero 文献读取

当用户请求“读文献 / 总结论文 / 基于 Zotero 条目问答”时，优先使用本技能。

## 适用场景

- 分析当前论文条目
- 提取某篇文献的关键信息
- 基于笔记与批注回答问题
- 按关键词检索 Zotero 库中的候选文献

## 脚本入口

使用 `scripts/zotero_literature_reader.py` 读取本地 Zotero 数据库并输出 JSON。

常用示例：

```bash
python firefly/skills/zotero/scripts/zotero_literature_reader.py --item-key ABCD1234
python firefly/skills/zotero/scripts/zotero_literature_reader.py --item-id 12345 --include-storage-text
python firefly/skills/zotero/scripts/zotero_literature_reader.py --query "attention is all you need" --limit 3
```

## 输出使用建议

- 先阅读 `fields`（标题、作者、年份、DOI 等）
- 再结合 `notes` 与 `annotations`
- 需要全文证据时：
  1. 优先 `read_file` **`backend/llm-wiki/raw/markdown/…/*.md`**（或消息中的 `[zotero_current_wiki_markdown_path=…]`）
  2. 或用 `zotero_read_item --include-storage-text`（内部同样 markdown → pdf → storage）
  3. 章节检索用 **`rag_search`**，不要用 `read_file` 直接读 `raw/pdf`
- **不要**对已有 markdown 镜像的文献使用 `read_file` 读 PDF
- 在回复中优先给出结构化结论，再给关键证据片段
