# 文献 markdown：用 grep 定位，禁止凭 offset 猜

## 问题来源

`backend/workspace/sessions/zotero_chat-1.jsonl`（2026-06-26）：用户要求总结论文 **§4 仿真实验** 并写入 `docx/`。Agent 未按章节标题定位，而是用 `read_file` 的 `offset`/`limit` 跳读。

| 轮次 | Agent 做法 | 实际读到 |
|------|-----------|----------|
| 用户反馈 docx 步骤空白 | `read_file(offset=100, limit=500)` | **§3 方法原理**，不是实验 |
| 用户说「没改文件」 | `read_file(offset=200, limit=500)` | 才读到 **§4 仿真实验与分析** |

## 根因

PDF→markdown 后的 **行号与论文章节号、PDF 页码不对应**。`offset=100` 不等于「第 100 页」或「第四章」。

原先桥接注入仅提示「`read_file` the markdown path」，未规定定位方式，大文件时模型容易乱设 offset。

## 后果

1. **内容错位**：未读到目标章节就声称已修复，docx 多为模板推断而非原文。
2. **幻觉式完成**：实验步骤、参数、流程来自模型补全，与用户要的「可复现」不符。
3. **反复新建文件**：未改用户指定路径，却新建 `…详细版.docx`、`…完整版.docx`（见 [docx-path.md](./docx-path.md)）。
4. **中间 assistant 草稿落盘**：带 `tool_calls` 的超长正文写入 JSONL（见 [draft-persist.md](./draft-persist.md)）。

## 正确流程

1. **`grep`**：`path` = 该 `.md`，`output_mode=content`，用标题/关键词（如 `仿真实验`、`## **4**`、`实验步骤`、`Tab. 1`）。
2. **`read_file`**：用 grep 输出中的 **行号** 作为 `offset`，再设合理 `limit` 精读。
3. 若问题可由 RAG 覆盖，优先 **`rag_search`**，减少手工翻页。

```text
# 错误
read_file(path, offset=200, limit=500)   # 猜章节

# 正确
grep(pattern="仿真实验", path=path, output_mode="content")
read_file(path, offset=<grep 行号>, limit=400)
```

## 已落实的改动（2026-06-26）

- `zotero_interface/commands.py`：`[zotero_literature_read_hint]` 要求先 grep 再 read_file。
- `templates/agent/identity.md`（Zotero）：「Navigating long paper markdown」小节。
- `read_file` / `grep` 工具 description。
- `skills/markdown/SKILL.md`、`skills/zotero/SKILL.md`。

重启 FireFly bridge 后注入与系统提示生效。

## 相关

- [docx-path.md](./docx-path.md) — 同路径覆盖
- [docx-verify.md](./docx-verify.md) — 写完先验证再回复
