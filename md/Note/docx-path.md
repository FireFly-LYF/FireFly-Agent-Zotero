# docx：用户指定路径时同路径覆盖

## 问题来源

`backend/workspace/sessions/zotero_chat-1.jsonl`（2026-06-26）：用户要求把实验步骤写入 `docx/`，后续反馈内容空白、**「你没有修改这个文件」**。

| 轮次 | 用户意图 | Agent 做法 |
|------|----------|-----------|
| 1 | 写入 docx | 创建 `docx/实验步骤复现指南.docx` |
| 2 | 修复空白步骤 | **新建** `实验步骤复现指南详细版.docx`，未改第一份 |
| 3 | 没改原文件 | **再新建** `实验步骤复现指南完整版.docx` |

用户打开/关心的仍是第一份路径，Agent 却每次 `create_from_markdown` 换文件名，口头说「已修复」但原文件未变。

## 根因

1. **工具默认像「另存为」**：`create_from_markdown` 每次新建，模型未把「修订」映射为同路径覆盖。
2. **未走编辑链路**：未 `open_document` → 改段落 → `save_document(同路径)`。
3. **路径语义与用户不一致**：用户说「改这个文件」，Agent 理解为「再生成一份更好的」。

## 正确流程

用户已给出或隐含路径（如 `docx/实验步骤复现指南.docx`）时：

| 情况 | 做法 |
|------|------|
| 文件不存在 | `create_from_markdown(output_path=用户路径, …)` |
| 文件已存在，局部修改/补全 | `open_document(用户路径)` → `search_text` / `replace_text` / `insert_text` → `audit_document()` → **`save_document(output_path=用户路径)`** |
| 全文重写 | `create_from_markdown` **同一 `output_path`**，或打开后 bulk 替换再存同路径 |

禁止在用户未要求时自动加 `详细版`、`完整版` 等新文件名。

```text
# 错误
create_from_markdown(output_path="docx/实验步骤复现指南详细版.docx", ...)

# 正确
open_document("docx/实验步骤复现指南.docx")
# ... 编辑 ...
save_document(output_path="docx/实验步骤复现指南.docx")
```

首轮仅说「写入 docx 文件夹」时，后续修复轮次应沿用首轮创建的路径，不要另起新名。

## 已落实的改动（2026-06-26）

- `templates/agent/identity.md`：「Create vs revise (same path)」表。
- `skills/docx/SKILL.md`：「Same path — create vs revise」小节。

与 [grep.md](./grep.md) 中 issue 1（读错章节）独立：即使内容读对了，换文件名仍会导致「没改文件」。

写完 docx 后须 [打开验证再回复](./docx-verify.md)。
