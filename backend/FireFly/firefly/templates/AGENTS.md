# Agent 指令

## 定时提醒

在创建提醒前，先检查可用技能并优先遵循技能指引。
使用内置 `cron` 工具创建/列出/删除任务（不要通过 `exec` 调用 `firefly cron`）。
从当前会话获取 USER_ID 和 CHANNEL（例如从 `telegram:8281248569` 解析出 `8281248569` 与 `telegram`）。

**不要只把提醒写进 `MEMORY.md`** —— 这样不会触发真实通知。

## Heartbeat 任务

系统会按配置的 heartbeat 间隔检查 `HEARTBEAT.md`。请使用文件工具管理周期任务：

- **新增**：用 `edit_file` 追加任务
- **删除**：用 `edit_file` 删除已完成任务
- **重写**：用 `write_file` 替换全部任务

当用户要求“循环/周期性任务”时，应更新 `HEARTBEAT.md`，而不是创建一次性的 cron 提醒。
