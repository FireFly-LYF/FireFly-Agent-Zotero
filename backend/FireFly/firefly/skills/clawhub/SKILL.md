---
name: clawhub
description: 从 ClawHub 公共技能注册表搜索并安装技能。
homepage: https://clawhub.ai
metadata: {"firefly":{"emoji":"🦞"}}
---

# 爪轮

AI 代理的公共技能注册表。按自然语言搜索（矢量搜索）。

## 何时使用

当用户提出以下任何问题时使用此技能：
- “找到一项技能……”
- “寻找技能”
- “安装技能”
- “有什么技能可以使用？”
- “更新我的技能”

## 搜索

```bash
npx --yes clawhub@latest search "web scraping" --limit 5
```

## 安装

```bash
npx --yes clawhub@latest install <slug> --workdir ~/.firefly/workspace
```

将 `<slug>` 替换为搜索结果中的技能名称。这会将技能放入 `~/.firefly/workspace/skills/` 中，纳米机器人从中加载工作区技能。始终包含 `--workdir`。

## 更新

```bash
npx --yes clawhub@latest update --all --workdir ~/.firefly/workspace
```

## 已安装列表

```bash
npx --yes clawhub@latest list --workdir ~/.firefly/workspace
```

## 笔记

- 需要 Node.js（`npx` 附带）。
- 搜索和安装不需要 API 密钥。
- 仅发布时需要登录 (`npx --yes clawhub@latest login`)。
- `--workdir ~/.firefly/workspace` 至关重要 - 没有它，技能将安装到当前目录而不是纳米机器人工作区。
- 安装后，提醒用户启动新会话来加载技能。
