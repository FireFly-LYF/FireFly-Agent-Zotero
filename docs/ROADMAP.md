# 路线图

> 基于 2026-06 代码现状整理。与代码不一致时以代码行为为准。

## 项目定位

**目标**：将 Zotero 插件从「增强版文献问答」升级为「能自主规划、调用工具、写回结果的研究 Agent」。

**独特优势**（面试/简历应强调）：

1. **嵌入真实工作流**：研究人员日常使用的 Zotero 场景，非独立 Demo
2. **隐私优先本地架构**：TS 插件 + 本地 Python Bridge（`127.0.0.1:8765`）
3. **章节感知 RAG**：非 naive chunk，有章节扩展与编号加权逻辑
4. **自研 Agent 引擎**：编排/直接双模式、Subagent、Summarizer，非 LangChain 套壳

---

## 当前状态（2026-06）

### 已完成

| 模块 | 状态 | 关键路径 |
|------|------|----------|
| Zotero 插件 | ✅ | 聊天、划选、图片、KaTeX、流式 Bridge、设置页 |
| Agent 引擎 | ✅ | AgentLoop、ToolRegistry、Subagent、编排/直接模式 |
| 文献 RAG | ✅ | PDF→MD→JSONL；`rag_search` / `rag_index` Agent Tool |
| LLM Wiki | ✅ | `AGENTS.md`、镜像 wiki 注入、`_synthesis` 设计 |
| Zotero 读工具 | ✅ | `zotero_search`、`zotero_read_item` |
| Docx 写作 | ✅ | docx-mcp 集成 |
| Summarizer | ✅ | 回合结束汇总，隐藏 tool 草稿 |
| 发布 | ✅ | Windows 一键 ZIP |

### 进行中 / 待完善

| 项 | 说明 |
|----|------|
| 端到端 Demo | 「读论文 → 检索相关文献 → 写 survey 笔记」可录屏演示 |
| RAG 质量 | 特定小节误召第 1 章等，见 [ISSUE.md](ISSUE.md) |
| Zotero 写操作 | `zotero_create_note` 尚未实现 |
| 外部文献 Tool | arXiv / Semantic Scholar 待接入 |
| Wiki 维护闭环 | `_synthesis` 多文献关联待验证 |
| 前端 Tool UI | 动态展示 tool 进度仍可优化 |

---

## 优先级

### P0 — 可演示的 Agent 任务链（1–2 周）

1. 录制 2–3 分钟 Demo：Agent 自主 `rag_search` → `web_search` → 写 docx / 笔记
2. 前端完善 tool hints 展示（让用户看到 Agent 在做什么）
3. RAG 召回优化（见 ISSUE.md）

### P1 — 文献外部 Tool（3–5 天）

| Tool | 作用 |
|------|------|
| `arxiv_search` | 按关键词/作者检索 |
| `semantic_scholar_lookup` | DOI/标题查引用 |
| `zotero_create_note` | 写回 Zotero 笔记 |

### P2 — 质量巩固

- 分段展示只从最后一条 user 消息抽取 RAG
- 截图 OCR（当前仍为占位）
- Wiki `_synthesis` 多文献验证

### 暂缓

| 方向 | 原因 |
|------|------|
| 迁移 LangChain | 已有完整 Agent 循环，收益低 |
| 向量库 + 用户画像 | Dream 记忆已够用 |
| 文献知识图谱 | 投入大，Demo 之后 |

---

## Tool 清单

| Tool | 状态 |
|------|------|
| `rag_search` / `rag_index` / `get_markdown_headings` | ✅ |
| `zotero_read_item` / `zotero_search` | ✅ |
| `read_file` / `grep` / `glob` / `write_file` | ✅ |
| `web_search` / `web_fetch` | ✅ |
| docx-mcp | ✅ |
| `spawn` / `plan_tasks` | ✅ |
| `zotero_create_note` | 🔲 |
| `arxiv_search` | 🔲 |
| `semantic_scholar_lookup` | 🔲 |

---

## 4 周建议节奏

| 周次 | 任务 |
|------|------|
| W1 | 完善 Tool UI + RAG 召回修复 |
| W2 | arXiv Tool + Zotero 写笔记 Tool |
| W3 | 端到端 Demo 录制 + README 嵌入 GIF/链接 |
| W4 | Wiki synthesis 验证 + 文档收尾 |
