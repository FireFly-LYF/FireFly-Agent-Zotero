# FireFly-Agent-Zotero 路线图

> 本文档基于 2026-06 代码现状整理，用于指导后续开发方向与简历包装。以仓库实际代码为准；与代码不一致时以代码行为优先。

---

## 一、项目定位

**目标**：将 Zotero 插件从「增强版文献问答」升级为「能自主规划、调用工具、写回结果的研究 Agent」。

**独特优势**（面试/简历应强调）：

1. **嵌入真实工作流**：不是独立 demo，而是研究人员日常使用的 Zotero 场景。
2. **隐私优先本地架构**：TS 插件 + 本地 Python Bridge（`127.0.0.1:8765`），学术数据不出本机。
3. **章节感知 RAG**：非 naive chunk，有 `rag_utils.py` 章节扩展与编号加权逻辑。

---

## 二、当前现状（2026-06）

### 已完成

| 模块 | 状态 | 关键路径 |
|------|------|----------|
| Zotero 插件 | ✅ | 聊天、划选文字、图片上传、KaTeX、流式 Bridge、设置页编辑 config |
| Agent 引擎 | ✅ | 自研 `AgentLoop` + `ToolRegistry`，多轮 Tool、子 Agent、Web/Exec/MCP |
| 文献 RAG | ✅ | PDF→Markdown→JSONL；Bridge 自动注入 `[RAG Context]` |
| LLM Wiki | ✅ 规范完整 | `llm-wiki/AGENTS.md`、镜像 wiki 页注入、`_synthesis` 设计 |
| 记忆 | ✅ 基础版 | Dream 双层记忆（`MEMORY.md` / `USER.md` / `history.jsonl`） |
| 发布 | ✅ | Windows 一键 ZIP（XPI + wheel + 安装脚本），见 [INSTALL.md](INSTALL.md) |

### 核心差距

问题不在「有没有 Agent 框架」，而在 **Zotero 主路径没有真正用起来**：

| 差距 | 说明 |
|------|------|
| RAG 硬注入 | Bridge 层预塞 `[RAG Context]`，Agent 无法自主决定何时/如何检索 |
| Zotero Tool 缺失 | `skills/zotero/SKILL.md` 已写，但 `zotero_literature_reader.py` 未实现 |
| 无文献外部 Tool | 仅有 DuckDuckGo，无 arXiv / Semantic Scholar 等 |
| 无 Zotero 写操作 | 不能创建笔记、打标签、移动收藏 |
| Wiki 维护未闭环 | `_synthesis` 多文献关联尚未验证 |
| RAG 质量遗留 | 见 [ISSUE.md](ISSUE.md)：误召第 1 章、调试视图历史 RAG 混淆等 |

### 架构对比

**当前路径**（偏问答）：

```
插件 → Bridge(8765) → 预注入 RAG/Wiki/条目上下文 → AgentLoop → LLM
```

**目标路径**（偏 Agent）：

```
插件 → Bridge → AgentLoop → 自主规划 → 调用 Tool(RAG/arXiv/Zotero/…) → 整合 → 写回 Zotero
```

后端已具备第二条链路能力（`maxToolIterations: 200`，`web.enable: true`，`exec.enable: true`），需把 Zotero 体验从「预注入 RAG」切到「Agent 驱动 Tool」。

---

## 三、开发优先级（按投入产出比）

### P0：Zotero 场景接入 Agent 工具链（1–2 周）

**不必引入 LangChain**，沿用现有 FireFly Agent 即可。

1. **实现 `zotero_literature_reader.py`** ✅
   - 脚本：`backend/FireFly/firefly/skills/zotero/scripts/zotero_literature_reader.py`
   - 只读打开 `zotero.sqlite`（`?mode=ro`）；数据目录：`ZOTERO_DATA_DIR` 或自动探测
   - 输出：元数据（fields/creators/tags）、笔记、PDF 批注、可选附件文本（PDF/纯文本）
   - Agent Tool：`zotero_search`、`zotero_read_item`（`firefly/agent/tools/zotero.py`，已在 `AgentLoop` 注册）
   - CLI 示例：
     ```bash
     python firefly/skills/zotero/scripts/zotero_literature_reader.py --item-key ABCD1234
     python firefly/skills/zotero/scripts/zotero_literature_reader.py --query "attention" --limit 3
     ```

2. **RAG 从 Bridge 预注入改为 Tool**
   - 新增 `literature_rag_search(query, pdf_path)` Tool（从 `commands.py` 抽取现有检索逻辑）
   - Bridge 只保留轻量 marker（当前 PDF 路径、`item_id`），不再塞大段 `[RAG Context]`
   - Agent 可按需多次检索、换 query 重试

3. **前端展示工具调用进度**
   - 当前 `sendToolHints: false`，用户看不到 Agent 在做什么
   - Zotero 渠道开启 tool hints，或单独展示「正在检索 4.2 节…」

**验收标准**：用户问「比较本文 4.2 节与 3.1 节实验设置差异」，Agent 自主调用 RAG Tool 两次并对比，而非依赖 Bridge 一次性注入。

---

### P1：端到端「目标型任务」Demo（1 周）

简历与 GitHub 需有可录屏的 multi-step 演示。

**推荐 Demo 任务**：

> 基于当前打开的论文，写一份 short survey 笔记，保存到 Zotero 条目笔记里。

Agent 应自主完成：

1. RAG 读当前论文
2. `arxiv_search` 或 `web_search` 找 2–3 篇相关外部论文
3. 对比总结
4. 调用 Zotero Tool 写入笔记

产出：2–3 分钟录屏，放 GitHub README 或简历链接。

---

### P2：补齐文献外部 Tool（3–5 天）

| Tool | 作用 |
|------|------|
| `arxiv_search` | 按关键词/作者检索最新论文 |
| `semantic_scholar_lookup` | DOI/标题查引用与摘要 |
| `zotero_create_note` | 把 Agent 输出写回 Zotero |

3 个研究向 Tool + 现有 RAG/Web 即可支撑简历叙事；不必一次做 10+ 个。

---

### P3：质量巩固（可与 P0/P1 并行）

来自 [ISSUE.md](ISSUE.md) 与开发日志：

- [ ] RAG 分段展示只从最后一条 user 消息抽取（避免调试误解）
- [ ] 问特定小节时更 aggressively 过滤无关章节块
- [ ] LLM Wiki `_synthesis` 多文献关联验证
- [ ] 截图 OCR（开发日志里仍为占位）

---

### 暂缓方向

| 方向 | 原因 |
|------|------|
| 迁移 LangChain | 已有完整 Agent 循环，迁移成本高、收益低 |
| 向量库 + 用户画像 | Dream 记忆已够用；先跑通 Tool 链再升级 |
| 多 Agent 协作 | `SubagentManager` 已有，等单 Agent 任务稳定后再拆角色 |
| 文献知识图谱 | 投入大，简历加分有限，放在 Demo 之后 |

---

## 四、4 周路线图

| 周次 | 任务 |
|------|------|
| W1–W2 | Zotero Reader Tool + RAG Tool 化 + 前端 Tool 进度 |
| W2–W3 | arXiv Tool + Zotero 写笔记 Tool |
| W3 | Short Survey 端到端 Demo + 录屏 |
| 全程并行 | RAG 召回优化、Wiki synthesis 验证 |

---

## 五、Tool List（目标清单）

开发完成后建议在 README 或 `docs/TOOLS.md` 维护此列表，便于简历引用。

| Tool | 类型 | 状态 |
|------|------|------|
| `literature_rag_search` | 本地文献段落检索 | 🔲 待从 Bridge 抽取 |
| `zotero_read_item` | 读条目元数据/笔记/批注 | ✅ |
| `zotero_search` | 库内关键词检索 | ✅ |
| `zotero_create_note` | 写回 Zotero 笔记 | 🔲 未开始 |
| `arxiv_search` | 外部论文检索 | 🔲 未开始 |
| `semantic_scholar_lookup` | 引用/摘要查询 | 🔲 未开始 |
| `web_search` / `web_fetch` | 通用网络检索 | ✅ 已有 |
| `read_file` / `grep` / `glob` | 工作区与 Wiki 文件 | ✅ 已有 |
| `spawn` | 子 Agent 委派 | ✅ 已有（暂未用于 Zotero 研究流） |

---

## 六、简历包装（Agent 视角）

写简历时遵循 **「目标 → 架构 → 行动 → 成果」**，强调 Agent 特性而非功能罗列。

### 项目概述（1–2 句）

> **Zotero Research Agent — 基于 LLM 的自主文献研究智能体**  
> 为 Zotero 构建具备任务规划、工具调用、多模态感知与长期记忆的个人研究助手，将传统文献管理软件升级为主动式学术协作 Agent。

### 核心贡献（按 Agent 能力组织）

- **多步推理与自主任务执行**：自研 Agent 引擎，支持复杂研究目标（多文献对比、short survey）的自动拆解、工具调度与端到端执行。
- **工具增强生态**：本地 RAG（章节感知）、Zotero 读写、arXiv/网络检索等 Tool，Agent 动态决定调用时机与次数。
- **双层记忆系统**：Dream 管理的短期对话 + 长期用户/项目记忆（`MEMORY.md` / `history.jsonl`）。
- **多模态文献理解**：划选段落、图片上传，结合 VL 模型解读图表与公式。
- **插件化前端 + 本地后端**：TypeScript Zotero 插件 + Python Bridge，保证研究数据隐私。

### 技术栈

TypeScript, Python, Zotero 7 Plugin API, FastAPI/aiohttp Bridge, ChromaDB/FAISS（RAG 索引）, OpenAI 兼容 LLM, Docker（可选）

### 成果（量化或定性）

- [ ] 支持 N 类研究任务的自动化（待 Demo 完成后填写）
- [ ] 本地 Agent 流式响应延迟 < 3s
- [ ] Multi-step 任务中 Agent 自主调用 ≥4 种 Tool、平均 ≥7 步推理（待 Demo 验证）

### 面试应答：「和普通 RAG 问答有什么不同？」

从五个维度拆解：

1. **感知**：划选、图片、当前打开 PDF、Zotero 条目上下文
2. **规划**：用户给目标，Agent 拆解步骤（非单轮 Q&A）
3. **记忆**：跨会话 Dream 记忆 + 文献 Wiki 沉淀
4. **工具**：RAG / Zotero / arXiv / Web 动态调用
5. **行动**：写回 Zotero 笔记、维护 Wiki（而非只输出聊天文本）

---

## 七、相关文档

| 文档 | 用途 |
|------|------|
| [README.md](README.md) | 开发日志 |
| [INSTALL.md](INSTALL.md) | 用户安装 |
| [ISSUE.md](ISSUE.md) | RAG 召回等问题备忘 |
| [backend/llm-wiki/AGENTS.md](backend/llm-wiki/AGENTS.md) | Wiki 维护规范 |
| [backend/FireFly/firefly/skills/zotero/SKILL.md](backend/FireFly/firefly/skills/zotero/SKILL.md) | Zotero 技能（待实现脚本） |
