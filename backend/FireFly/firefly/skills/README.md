# firefly 技能

本目录包含用于扩展 firefly 能力的内置技能。

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

## Agent 实际读取方式

Agent 通过 `firefly/agent/skills.py` 的 `SkillsLoader.list_skills()` 动态扫描技能目录，
读取 `skills/<skill_name>/SKILL.md` 作为可用技能来源（并支持 workspace 覆盖同名内置技能）。

## 当前内置技能列表

| 技能 | 说明 |
|------|------|
| `clawhub` | 从 ClawHub 公共技能注册表搜索并安装技能 |
| `cron` | 安排提醒与重复任务 |
| `github` | 使用 `gh` CLI 与 GitHub 交互 |
| `markdown` | PDF 转 Markdown、Markdown 切片到 RAG |
| `memory` | 由 Dream 管理的双层记忆系统 |
| `skill-creator` | 创建或更新 Agent Skills |
| `tmux` | 远程控制 tmux 会话（交互式 CLI） |
| `wiki` | llm-wiki 的 PDF/Markdown 处理流程 |
| `zotero` | 读取 Zotero 文献元数据、笔记与批注 |
