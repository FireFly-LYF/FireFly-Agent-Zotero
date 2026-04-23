---
name: cron
description: 安排提醒与重复任务。
---

# 克朗

使用 `cron` 工具安排提醒或重复任务。

## 三种模式

1. **提醒** - 消息直接发送给用户
2. **任务** - 消息是任务描述，代理执行并发送结果
3. **一次性** - 在特定时间运行一次，然后自动删除

## 示例

固定提醒：
```
cron(action="add", message="Time to take a break!", every_seconds=1200)
```

动态任务（agent每次执行）：
```
cron(action="add", message="Check HKUDS/firefly GitHub stars and report", every_seconds=600)
```

一次性计划任务（从当前时间计算 ISO 日期时间）：
```
cron(action="add", message="Remind me about the meeting", at="<ISO datetime>")
```

时区感知 cron：
```
cron(action="add", message="Morning standup", cron_expr="0 9 * * 1-5", tz="America/Vancouver")
```

列出/删除：
```
cron(action="list")
cron(action="remove", job_id="abc123")
```

## 时间表达式

|用户说 |参数|
|-----------|------------|
|每 20 分钟 |每秒：1200 |
|每小时 |每秒：3600 |
|每天早上 8 点 | cron_expr：“0 8 * * *”|
|工作日下午 5 点 | cron_expr：“0 17 * * 1-5”|
|温哥华时间每日上午 9 点 | cron_expr: "0 9 * * *", tz: "美国/温哥华" |
|在特定时间 | at：ISO 日期时间字符串（从当前时间计算）|

## 时区

将 `tz` 与 `cron_expr` 结合使用可在特定 IANA 时区进行安排。如果没有 `tz`，则使用服务器的本地时区。
