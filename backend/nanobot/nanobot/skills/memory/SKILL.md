---
name: memory
description: 由 Dream 管理知识文件的双层记忆系统。
always: true
---

# 记忆

## 结构

- `SOUL.md` — 机器人的个性和通信风格。 **由 Dream 管理。 ** 请勿编辑。
- `USER.md` — 用户个人资料和偏好。 **由 Dream 管理。 ** 请勿编辑。
- `memory/MEMORY.md` — 长期事实（项目背景、重要事件）。 **由 Dream 管理。 ** 请勿编辑。
- `memory/history.jsonl` — 仅附加 JSONL，未加载到上下文中。最好使用内置的 `grep` 工具来搜索它。

## 搜索过去的活动

`memory/history.jsonl` 是 JSONL 格式 — 每行都是一个带有 `cursor`、`timestamp`、`content` 的 JSON 对象。

- 对于广泛的搜索，请从 `grep(..., path="memory", glob="*.jsonl", output_mode="count")` 或默认的 `files_with_matches` 模式开始，然后再扩展到完整内容
- 当您需要精确匹配的行时，请使用 `output_mode="content"` 加上 `context_before` / `context_after`
- 使用 `fixed_strings=true` 作为文字时间戳或 JSON 片段
- 使用 `head_limit` / `offset` 翻阅长历史记录
- 仅当内置搜索无法表达您需要的内容时，才使用 `exec` 作为最后的后备手段

示例（替换 `keyword`）：
- `grep("keyword", path="memory", glob="*.jsonl", output_mode="count")`
- `grep("keyword", path="memory", glob="*.jsonl", output_mode="files_with_matches")`
- `grep("keyword", path="memory", glob="*.jsonl", output_mode="content", context_before=2, context_after=2)`
- `grep("2026-04", path="memory", glob="*.jsonl", output_mode="content", fixed_strings=true, head_limit=50, offset=0)`

## 重要的

- **请勿编辑 SOUL.md、USER.md 或 MEMORY.md。 ** 它们由 Dream 自动管理。
- 如果您发现过时的信息，它将在 Dream 下次运行时进行更正。
- 用户可以使用 `/dream-log` 命令查看 Dream 的活动。
