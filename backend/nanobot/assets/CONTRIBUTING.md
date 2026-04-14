# nanobot 贡献指南

感谢你参与 nanobot 的建设。

我们希望代码保持：简洁、可读、可维护。提交贡献时请优先做“小而清晰”的改动。

## 维护分支

| 分支 | 用途 |
|------|------|
| `main` | 稳定版本与修复 |
| `nightly` | 实验特性与较大改动 |

## 提交到哪个分支

- 新功能、重构、可能影响行为的变更：提交到 `nightly`
- 文档改进、小型修复、无行为变更的 bugfix：提交到 `main`
- 不确定时：优先 `nightly`

## 本地开发

```bash
git clone https://github.com/HKUDS/nanobot.git
cd nanobot
pip install -e ".[dev]"
pytest
ruff check nanobot/
ruff format nanobot/
```

## 代码风格

- 追求可读性，避免炫技式实现
- 优先最小必要改动，避免过度重构
- Python 版本目标：3.11+
- 建议保持单行 100 字符以内（ruff 配置）

## 提交前检查

- 测试通过：`pytest`
- Lint 通过：`ruff check`
- 如有格式化需求：`ruff format`

## 获取帮助

- 问题：<https://github.com/HKUDS/nanobot/issues>
- 社区：见 `COMMUNICATION.md`
- 邮件：xubinrencs@gmail.com
