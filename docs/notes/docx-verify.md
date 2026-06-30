# docx：写完先验证，再回复用户

## 问题来源

`backend/workspace/sessions/zotero_chat-1.jsonl`（2026-06-26）：

| 轮次 | 用户 | Agent |
|------|------|--------|
| 2 | 「docx 里很多步骤是空白」 | 未打开 docx 检查，声称「已修复」，新建 `…详细版.docx` |
| 3 | 「你没有修改这个文件」 | 仍未验证原路径文件，再新建 `…完整版.docx` |

`create_from_markdown` 的 tool 结果虽有 `paragraph_count` / `heading_count`，Agent 未据此或打开文档核对正文是否满足用户要求（具体步骤、无空白节）。

## 根因

1. **写完即宣告完成**：把 MCP 返回的 success 当成用户问题已解决。
2. **未用 docx-mcp 读回**：`.docx` 不能用 `read_file`；应 `open_document` + `get_headings` / `search_text` / `get_paragraph`。
3. **未对照用户诉求做锚点检查**：用户要「可复现实验步骤」，应搜索 `步骤1`、`仿真实验` 等，确认非空。

## 正确流程

```
create_from_markdown / save_document
  → open_document(用户路径)
  → get_document_info() / get_headings()
  → search_text(用户关心的标题/关键词)
  → 若空白或缺失 → 编辑 → save → 再验证
  → 验证通过后再回复用户
```

## 与相关 issue 的关系

- [grep.md](./grep.md)：先读对论文 §4，docx 内容才有依据。
- [docx-path.md](./docx-path.md)：写到用户指定的同一路径。
- 本 issue：写完后**打开验证**，避免「口头修复、文件仍空」。

## 已落实（2026-06-26）

- `templates/agent/identity.md`：「Verify before reply (mandatory)」
- `skills/docx/SKILL.md`：「Verify deliverable before replying」

重启 FireFly bridge 后生效。
