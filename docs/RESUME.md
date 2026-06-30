# 简历 / 面试包装指南

> 将 FireFly-Agent-Zotero 写进简历或面试自我介绍时的参考模板。按 **「目标 → 架构 → 行动 → 成果」** 组织，强调 Agent 特性而非功能罗列。

---

## 项目概述（1–2 句）

**Zotero Research Agent — 基于 LLM 的自主文献研究智能体**

为 Zotero 构建具备任务规划、工具调用、多模态感知与长期记忆的个人研究助手，将传统文献管理软件升级为主动式学术协作 Agent。全部推理与文献数据保留在本机，通过本地 Bridge 对接云端 LLM API。

---

## 核心贡献（按 Agent 能力组织）

### 多步推理与自主任务执行

- 自研 Agent 引擎（`AgentLoop` + `AgentRunner`），支持复杂研究目标的自动拆解与多轮 Tool Calling
- 编排/直接双模式：简单问答直接执行；多阶段任务通过 `plan_tasks` + `spawn` 委派 Subagent
- 回合 Summarizer 将 tool 轨迹压缩为用户可读回复，避免暴露中间草稿

### 工具增强生态

- 章节感知 RAG：PDF → Markdown → JSONL，`rag_search` 按章节号加权召回
- Zotero 读写：`zotero_search` / `zotero_read_item` 直连 SQLite 读元数据与批注
- 文档写作：集成 docx-mcp，支持 Markdown 转 Word 与带修订编辑
- Web 检索、文件系统、MCP 等通用 Tool 扩展

### 插件化前端 + 本地后端

- TypeScript Zotero 7 插件：侧栏聊天、划选/图片多模态、KaTeX、SSE 流式
- Python Bridge（127.0.0.1:8765）保证学术数据隐私
- Windows 一键安装包（XPI + wheel + 脚本），GitHub Actions 自动发布

### 文献知识库（LLM Wiki）

- 设计 `raw/` + `wiki/` + `_synthesis` 三层知识结构
- Agent 可维护跨篇综述与单篇分析页，支持可追溯的 evidence link

---

## 技术栈（简历关键词）

```
TypeScript · Python · Zotero 7 Plugin API · aiohttp · Pydantic
Agent Tool Calling · SSE 流式 · PyMuPDF4LLM · MCP · pytest
```

---

## 成果表述（示例，按实际填写）

- 自研 Agent 框架，131+ 单元测试覆盖 RAG 召回、编排门控、会话管理
- 章节感知 RAG 在小节级问答中准确召回目标段落（可附测试用例或 Demo）
- Multi-step 任务中 Agent 自主调用 ≥4 种 Tool（待 Demo 完成后量化）
- [ ] 支持 N 类研究任务自动化（待 Demo 完成后填写）

---

## 面试高频问题

### 「和普通 RAG 问答有什么不同？」

从五个维度拆解：

1. **感知**：划选、图片、当前 PDF、Zotero 条目上下文
2. **规划**：用户给目标，Agent 拆解步骤（非单轮 Q&A）
3. **记忆**：跨会话 Dream 记忆 + 文献 Wiki 沉淀
4. **工具**：RAG / Zotero / Web / Docx 动态调用
5. **行动**：写回 docx / Wiki（而非只输出聊天文本）

### 「为什么不用 LangChain？」

- 已有完整的 `AgentLoop` + `ToolRegistry` + Subagent 体系
- Zotero 渠道有特殊的上下文注入、历史裁剪、双模式门控，自研更可控
- 131+ 测试覆盖核心路径，迁移成本高、收益低

### 「如何保证隐私？」

- Bridge 仅监听 `127.0.0.1`
- 文献 PDF / RAG 索引 / Wiki 全部在本地 `llm-wiki/`
- 仅 LLM API 请求出网；config 含 API Key 不入库

### 「RAG 召回不准怎么办？」

- 章节号加权 + 路径链扩展（`rag_utils.py`）
- Agent 可多次调用 `rag_search` 换 query，或 `get_markdown_headings` + `read_file` 精确定位
- 详见 [ISSUE.md](ISSUE.md) 中的已知问题与对策

---

## GitHub / 作品集建议

1. README 顶部放架构图 + 30 秒 Demo GIF（或 B 站/YouTube 链接）
2. Releases 提供可一键安装的 Windows 包，降低面试官试用门槛
3. `docs/ARCHITECTURE.md` 便于技术深挖
4. 在简历中附 GitHub 链接 + 一句话描述 + 技术栈标签

---

## 英文版 Elevator Pitch（可选）

> Built an on-device AI research agent for Zotero: a TypeScript plugin talks to a local Python agent engine over SSE, with chapter-aware RAG, subagent orchestration, and docx output — keeping academic data on localhost while using cloud LLMs for reasoning.
