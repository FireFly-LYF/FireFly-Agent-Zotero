# 架构设计

> 本文档描述 FireFly-Agent-Zotero 的核心模块与数据流，便于面试讲解与二次开发。

## 1. 总体分层

```
Plugin (TS)  →  Bridge (Python)  →  AgentLoop  →  Tools / Skills  →  llm-wiki
```

| 层 | 目录 | 职责 |
|----|------|------|
| 表现层 | `plugin/` | Zotero UI、用户输入、SSE 流式渲染 |
| 接入层 | `firefly/zotero_interface/` | HTTP Bridge、上下文注入、会话路由 |
| 推理层 | `firefly/agent/` | AgentLoop、Runner、Subagent、Summarizer |
| 工具层 | `firefly/agent/tools/` | RAG、Zotero、Web、FS、MCP、Spawn |
| 知识层 | `backend/llm-wiki/` | PDF/Markdown/RAG 索引与 Wiki 页 |

## 2. 插件 ↔ Bridge 通信

**入口文件**

- 插件：`plugin/src/modules/fireflyBridge.ts`、`itemPaneLLMUI.ts`
- Bridge：`firefly/zotero_interface/commands.py`（`agent` 命令内嵌 aiohttp 服务）
- 启动器：`backend/FireFly/scripts/zotero_bridge_launcher.py`

**协议**

- 健康检查：`GET /health`
- 聊天：`POST /zotero/message`（SSE 流式响应）
- 请求体含 `message`、`session_id`、可选 `media`（图片路径）

**Bridge 注入的上下文**

- 当前 Zotero 条目元数据（`zotero_current_item` 等 marker）
- 当前 PDF 对应的 wiki / markdown 路径
- Wiki 页内容（若存在镜像页）

Agent 在此基础上自主决定是否调用 `rag_search` 等工具，而非仅依赖预注入大段文本。

## 3. Agent 引擎

### 3.1 核心循环

`AgentLoop`（`firefly/agent/loop.py`）负责：

1. 接收 InboundMessage（来自 Bridge 或 CLI）
2. 构建 LLM 上下文（`ContextBuilder`）
3. 委托 `AgentRunner` 执行多轮 Tool Calling
4. 持久化会话（`SessionManager`）
5. 可选 Summarizer 生成用户可见的最终回复

### 3.2 双模式（Zotero 渠道）

| 模式 | 触发 | 主 Agent 可用工具 | 典型场景 |
|------|------|-------------------|----------|
| 直接模式 | 默认 | RAG、FS、Web、Docx、Zotero 等 | 单步问答、简单检索 |
| 编排模式 | `plan_tasks` 后 | 仅 `plan_tasks`、`spawn`、`await_stage` | 文献抽取 → docx 写作等多阶段任务 |

编排模式下，执行细节由 Subagent 完成（`firefly/agent/subagent.py`），主 Agent 只负责规划与汇总。

### 3.3 Summarizer

复杂任务结束后，`Summarizer`（`firefly/agent/summarizer.py`）将 tool 轨迹与用户问题压缩为简洁的最终回复，避免把中间草稿直接展示给用户。

### 3.4 Tool Registry

`ToolRegistry`（`firefly/agent/tools/registry.py`）统一管理工具注册与 subset 过滤。Zotero 渠道通过 `ZoteroSessionToolRegistry` 按模式动态切换可用工具集。

## 4. RAG 管线

```
PDF (raw/pdf/)  →  PyMuPDF4LLM  →  Markdown (raw/markdown/)
                                        ↓
                              rag_utils.py 切分 + 章节号
                                        ↓
                              JSONL 索引 (raw/rag/)
                                        ↓
                              rag_search Tool 召回
```

**章节感知召回**（`firefly/skills/markdown/scripts/rag_utils.py`）

- 按 Markdown 标题层级切分，保留 `chunk_index` 与路径链
- 对用户问题中的章节号（如 4.2）加权排序
- 支持章节扩展（父节 → 子节）与 fallback

**相关工具**

- `rag_index`：构建/重建索引
- `rag_search`：语义 + 章节号混合检索
- `get_markdown_headings`：获取文档大纲

## 5. LLM Wiki

规范入口：`backend/llm-wiki/AGENTS.md`

- `raw/`：只读原始层（PDF、Markdown、RAG 索引）
- `wiki/`：LLM 维护的分析页，目录与 `raw/markdown` 镜像
- `wiki/_synthesis/`：跨篇主题综述

Bridge 在问答时可注入 wiki 页摘要；Agent 也可通过 `read_file` / `grep` 直接访问。

## 6. 配置与运行时

| 文件 | 用途 |
|------|------|
| `backend/config/config.json` | LLM Provider、Agent 默认参数（**含 API Key，不入库**） |
| `backend/config/user.json` | 用户偏好（RAG 参数、调试开关） |
| `backend/config/context.json` | onboard 后的 config/workspace 路径 |
| `backend/workspace/` | 会话 JSONL、临时文件、记忆（运行时，不入库） |

## 7. 测试

- 后端：`backend/FireFly/tests/`（pytest，131+ 测试文件）
- 重点覆盖：RAG 章节召回、Orchestrator 门控、Summarizer、Zotero 历史脱敏
- 插件：`npm run typecheck` + Mocha 单元测试

## 8. 发布

GitHub Actions 在 `plugin/npm run release` 推送 tag 后：

1. 构建 XPI
2. 打包 `firefly_ai` wheel
3. 复制 llm-wiki 模板与 `release/*.ps1`
4. 上传 `FireFly-Agent-Zotero-vX.Y.Z-windows.zip`
