# 工具使用说明

工具签名会通过函数调用自动提供。
本文件记录不那么直观的限制与使用模式。

## exec — 安全限制

- 命令有可配置超时（默认 60 秒）
- 危险命令会被拦截（如 rm -rf、格式化、dd、shutdown 等）
- 输出最多保留 10,000 个字符
- 可通过 `restrictToWorkspace` 将文件访问限制在工作区内

## glob — 文件发现

- 优先用 `glob` 按模式找文件，再考虑 shell 命令
- 如 `*.py` 这类简单模式会按文件名递归匹配
- 当你需要目录而非文件时，用 `entry_type="dirs"`
- 对大结果集使用 `head_limit` 和 `offset` 分页
- 仅需要文件路径时，优先 `glob` 而不是 `exec`

## grep — 内容搜索

- 用 `grep` 在工作区内搜索文件内容
- 默认仅返回匹配文件路径（`output_mode="files_with_matches"`）
- 支持可选 `glob` 过滤与 `context_before` / `context_after`
- 支持 `type="py"`、`type="ts"`、`type="md"` 等简写过滤
- 搜索包含正则特殊字符的字面量时，用 `fixed_strings=true`
- 仅看路径时使用 `output_mode="files_with_matches"`
- 在读取完整匹配前，可先用 `output_mode="count"` 估算范围
- 对结果分页可用 `head_limit` 与 `offset`
- 代码与历史搜索优先 `grep`，不要先走 `exec`
- 二进制或超大文件可能被跳过，以保持结果可读

## cron — 定时提醒

- 用法请参考 cron 技能文档。
