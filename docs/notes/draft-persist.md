# 会话持久化：不将 tool-call 中间稿写入用户可见历史

## 问题来源

`backend/workspace/sessions/zotero_chat-1.jsonl`（2026-06-26）：一轮对话含多次 tool 调用时，每条带 `tool_calls` 的 assistant 都会把**超长正文**（含整份 docx 用 markdown、步骤草稿）写入会话 JSONL。

示例（行 4）：assistant 在调用 `mcp_docx-mcp_create_from_markdown` 前，正文已是数万字的实验步骤 + 内嵌 docx markdown。

## 后果

1. **插件历史**：`/zotero/history` 对每条 assistant 都序列化，用户刷新后看到多段重复/中间草稿气泡。
2. **Token 膨胀**：下一轮 LLM 上下文带上这些中间稿，浪费 token、易重复旧模板。
3. **与 UI 不一致**：流式界面只展示最终总结，但落盘却保留全部中间 assistant。

## 策略

| 层级 | 做法 |
|------|------|
| **落盘**（`literature_title` 的 Zotero 文献会话） | 带 `tool_calls` 且正文超 800 字 → 压成 `[Tool step: read_file, …]`；`reasoning_content` 超 2000 字截断 |
| **LLM 历史** | `_sanitize_history_for_llm` 对旧数据再压一层 |
| **用户可见**（`/zotero/history`） | 跳过带 `tool_calls` 的 assistant，只展示 user + 最终 assistant |

`tool` 消息与 `tool_calls` 结构仍保留在 JSONL 内，供 agent 续跑与删除整轮；只是不再把中间 narration 当作用户对话展示。

## 已落实（2026-06-26）

- `zotero_interface/utils.py`：`compact_tool_call_assistant_for_storage`、`is_zotero_user_visible_message`
- `agent/loop.py`：`_save_turn`、`_sanitize_history_for_llm`
- `zotero_interface/commands.py`：`_serialize_session_messages` 过滤

重启 FireFly bridge 后对新完成的轮次生效；已有 JSONL 在下次写入同会话时，新轮次按新规则落盘，旧中间条仍可能在文件中直至清理会话。
