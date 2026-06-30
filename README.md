# FireFly-Agent-Zotero

[![Zotero 7](https://img.shields.io/badge/Zotero-7-green?style=flat-square&logo=zotero&logoColor=CC2936)](https://www.zotero.org)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-blue?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.9-blue?style=flat-square&logo=typescript&logoColor=white)](https://www.typescriptlang.org/)
[![License](https://img.shields.io/badge/License-AGPL--3.0-orange?style=flat-square)](plugin/LICENSE)

**面向 Zotero 的本地 AI 研究 Agent** — 在文献管理软件中嵌入具备任务规划、工具调用、章节感知 RAG 与多模态理解能力的研究助手，学术数据全程保留在本机。

---

## 项目亮点

| 维度 | 说明 |
|------|------|
| **真实工作流嵌入** | 不是独立 Demo，而是集成在研究人员日常使用的 Zotero 7 条目侧栏 |
| **隐私优先** | TypeScript 插件 + 本地 Python Bridge（`127.0.0.1:8765`），API Key 与文献数据不出本机 |
| **Agent 架构** | 自研 `AgentLoop`：多轮 Tool Calling、编排/直接双模式、Subagent 委派、回合 Summarizer |
| **章节感知 RAG** | PDF → Markdown → JSONL 索引，按章节号加权召回，Agent 自主调用 `rag_search` |
| **文献知识库** | LLM Wiki 规范维护跨篇综述与单篇分析页 |
| **文档写作** | 集成 docx-mcp，支持 Markdown 转 Word 与带修订的文档编辑 |

---

## 系统架构

```
┌─────────────────────────────────────────────────────────────┐
│  Zotero 7 插件 (TypeScript)                                  │
│  · 条目侧栏聊天 · 划选文字/图片 · KaTeX · 流式 SSE           │
│  · 设置页编辑 config · 多标签会话                            │
└──────────────────────────┬──────────────────────────────────┘
                           │ HTTP SSE  127.0.0.1:8765
┌──────────────────────────▼──────────────────────────────────┐
│  Zotero Bridge (Python / aiohttp)                            │
│  · 注入当前文献上下文 · 会话管理 · 流式推送 tool hints       │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│  FireFly Agent Engine                                        │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────────────┐ │
│  │ AgentLoop   │→ │ ToolRegistry │→ │ LLM Provider        │ │
│  │ 编排/直接    │  │ RAG/Zotero/  │  │ (OpenAI 兼容)       │ │
│  │ Subagent    │  │ Web/Docx/FS  │  │                     │ │
│  └─────────────┘  └──────────────┘  └─────────────────────┘ │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│  llm-wiki/  ·  raw/pdf · raw/markdown · raw/rag · wiki/     │
└─────────────────────────────────────────────────────────────┘
```

**Agent 执行模式**

- **直接模式**：主 Agent 自行调用 `rag_search`、`read_file`、docx 等工具，适合单步问答与简单检索。
- **编排模式**：`plan_tasks` 规划阶段 → 按阶段 `spawn` 子 Agent（文献抽取 / docx 写作等 profile）→ Summarizer 汇总最终回复。

---

## 功能概览

### 前端（Zotero 插件）

- 条目侧栏 LLM 聊天面板，支持多标签会话
- PDF 阅读器划选文字、图片上传作为多模态上下文
- KaTeX 公式渲染（含窄栏自动换行）
- 流式展示 Agent 回复、Thinking 与 Tool 调用进度
- 设置页在线编辑 `config.json` / `user.json` / `context.json`

### 后端（FireFly Agent）

| 工具 | 用途 |
|------|------|
| `rag_search` / `rag_index` / `get_markdown_headings` | 章节感知文献检索 |
| `zotero_search` / `zotero_read_item` | 读取 Zotero 库元数据、笔记、批注 |
| `read_file` / `grep` / `glob` | 工作区与 Wiki 文件操作 |
| `web_search` / `web_fetch` | 外部信息检索 |
| docx-mcp | Word 文档读写与修订 |
| `spawn` / `plan_tasks` | 子 Agent 委派与多阶段编排 |
| `exec` / MCP | Shell 与外部 MCP 服务 |

### 文献知识库（LLM Wiki）

- `raw/`：PDF / Markdown / RAG 索引（与 Zotero 目录镜像）
- `wiki/`：LLM 维护的单篇分析页与 `_synthesis` 跨篇综述
- 详见 [`backend/llm-wiki/README.md`](backend/llm-wiki/README.md)

---

## 快速开始

### 用户安装（Windows 一键包）

1. 在 [GitHub Releases](https://github.com/FireFly-LYF/FireFly-agent-zotero/releases) 下载 `FireFly-Agent-Zotero-vX.Y.Z-windows.zip`
2. 解压后运行 `install-backend.ps1` → 配置 API Key → `start-bridge.ps1`
3. 在 Zotero 中安装 `plugin/fire-fly-agent-zotero.xpi`

完整步骤见 [INSTALL.md](INSTALL.md)。

### 开发者（源码）

```powershell
# 后端
cd backend\FireFly
python -m venv .venv
.\.venv\Scripts\activate
pip install -e ".[api,pdf]"
firefly onboard -c ..\config\config.json -w ..\workspace
python scripts\zotero_bridge_launcher.py

# 前端
cd ..\..\plugin
npm install
npm start
```

---

## 项目结构

```
FireFly-Agent-Zotero/
├── plugin/                 # Zotero 7 插件（TypeScript）
│   ├── src/modules/        # 聊天 UI、Bridge 通信、Wiki 同步
│   └── addon/              # 静态资源、本地化、manifest
├── backend/
│   ├── FireFly/            # Agent 引擎（Python，131+ 单元测试）
│   │   └── firefly/
│   │       ├── agent/      # AgentLoop、Runner、Subagent、Summarizer
│   │       ├── zotero_interface/  # Bridge 与 Zotero 命令
│   │       └── skills/     # markdown、docx、zotero、wiki 等 Skill
│   ├── llm-wiki/           # 文献知识库模板与规范
│   ├── config/             # 运行时配置（config.json 不入库）
│   └── download/docx-mcp/  # Word 文档 MCP 服务
├── release/                # Windows 安装脚本
└── docs/                   # 架构、路线图、简历包装等文档
```

---

## 技术栈

| 层级 | 技术 |
|------|------|
| 前端 | TypeScript, Zotero 7 Plugin API, KaTeX, zotero-plugin-scaffold |
| 后端 | Python 3.11+, aiohttp, Pydantic, LiteLLM 兼容 Provider |
| Agent | 自研 ToolRegistry + AgentLoop（非 LangChain） |
| RAG | PyMuPDF4LLM, 章节号加权 JSONL 索引 |
| 通信 | HTTP SSE 流式 Bridge（127.0.0.1:8765） |
| 测试 | pytest（131 文件）, Mocha（插件 typecheck） |
| 发布 | GitHub Actions → Windows ZIP 一体包 |

---

## 文档索引

| 文档 | 说明 |
|------|------|
| [INSTALL.md](INSTALL.md) | 用户安装指南 |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 架构设计与模块说明 |
| [docs/ROADMAP.md](docs/ROADMAP.md) | 路线图与待办 |
| [docs/RESUME.md](docs/RESUME.md) | 简历 / 面试包装要点 |
| [docs/CHANGELOG.md](docs/CHANGELOG.md) | 开发日志 |
| [docs/ISSUE.md](docs/ISSUE.md) | 已知问题与技术备忘 |
| [backend/llm-wiki/AGENTS.md](backend/llm-wiki/AGENTS.md) | Wiki 维护规范 |

---

## 许可证

- 插件： [AGPL-3.0-or-later](plugin/LICENSE)（基于 Zotero Plugin Template）
- 后端 FireFly： [MIT](backend/FireFly/LICENSE)

---


