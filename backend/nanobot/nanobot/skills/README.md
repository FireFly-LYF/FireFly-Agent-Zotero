# nanobot 技能

本目录包含用于扩展 nanobot 能力的内置技能。

## 技能格式

每个技能都是一个目录，至少包含一个 `SKILL.md` 文件，通常包括：
- YAML frontmatter（如名称、描述、元数据）
- 面向 Agent 的 Markdown 使用说明

当技能需要引用大型本地文档或日志时，建议优先使用内置搜索工具先缩小范围，再加载完整文件：
- 先用 `grep(output_mode="count")` 或 `files_with_matches` 做粗筛
- 对大结果集使用 `head_limit`、`offset` 分页查看
- 在需要理解目录结构时使用 `glob(entry_type="dirs")`

## 致谢

这些技能体系借鉴自 [OpenClaw](https://github.com/openclaw/openclaw)。
技能格式与元数据设计尽量保持兼容，便于迁移与复用。

## 内置技能列表

| 技能 | 说明 |
|------|------|
| `github` | 使用 `gh` CLI 与 GitHub 交互 |
| `weather` | 使用 wttr.in 与 Open-Meteo 获取天气信息 |
| `summarize` | 总结 URL、文件和 YouTube 内容 |
| `tmux` | 远程控制 tmux 会话 |
| `clawhub` | 从 ClawHub 搜索并安装技能 |
| `skill-creator` | 创建与打包新技能 |
