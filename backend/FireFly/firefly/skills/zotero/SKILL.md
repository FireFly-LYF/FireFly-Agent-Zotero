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
- 需要全文证据时，按下列**来源优先级**（消息中含 llm-wiki 镜像标记时）：
  1. **总结 / 方法提取 / 多章节问答** → **`rag_search` 首选**（可 `ensure_index=true`）；用主题化 query（如「第3节 方法」「双向双滑窗」「STMF 公式」），**不要**用 grep 逐词扫全文
  2. **章节 Q&A** → `rag_search`（缺索引则 `rag_index`），路径来自 `[zotero_current_wiki_markdown_path=…]` / `[zotero_current_wiki_pdf_path=…]`
  3. **精读扩展** → 在 RAG 命中章节后，用 `read_file(path, offset=…, limit=…)` 补全公式与步骤；offset 来自 RAG 片段中的行号或单次精准 grep，**禁止**从 offset=1 顺序翻页找章节
  4. **元数据 / 笔记 / 批注** → `zotero_read_item`（`include_storage_text=true`）
  5. **PDF** → 仅当尚无 markdown 镜像
- **`grep`**：仅当 RAG 已定位章节但缺精确行号/公式锚点时使用；**不适合**总结类、方法综述类任务
- 在回复中优先给出结构化结论，再给关键证据片段
