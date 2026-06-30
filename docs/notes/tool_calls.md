# FireFly Agent 工具调用与日志笔记

## 1. 如何查看工具调用

### 1.1 运行时日志（`--logs`）

FireFly 默认关闭 `firefly` 命名空间的 loguru 日志，启动时需加 `--logs`：

```powershell
firefly zotero agent --config backend\config\config.json --workspace <workspace> --logs
firefly agent --logs
```

启用后可看到：

- `Tool call: tool_name({...})` — 每次执行工具前（参数截断至 200 字符）
- `ZOTERO_LLM_CONTEXT` — 仅 Zotero 会话（`session_key` 以 `zotero:` 开头）打印完整 LLM messages
- `LLM usage: ...` — DEBUG 级别，需 `$env:LOGURU_LEVEL="DEBUG"`

日志走 stderr，无默认 log 文件，可重定向：`... 2> firefly.log`

### 1.2 终端「LLM 请求快照」

由 `backend/config/user.json` 控制：

- `show_llm_input` — 普通 CLI `firefly agent` 是否展示；Zotero `agent` 命令始终展示
- `llm_input_print.tools: true` — 展示工具 definitions、assistant tool_calls、tool 返回

### 1.3 界面工具提示

`config.json` → `channels.sendToolHints: true` 可在对话进度中显示工具提示（非详细 log）。

### 1.4 会话文件 `sessions/*.jsonl`

路径：`backend/workspace/sessions/cli_direct.jsonl`（session key `cli:direct`）

每行一条 JSON：

| 字段 | 含义 |
|------|------|
| `role: "assistant"` + `tool_calls` | 模型发起的工具调用 |
| `role: "tool"` + `tool_call_id` | 工具返回（与上方 `id` 配对） |
| `reasoning_content` | 模型推理（可选） |

快速搜索：`"tool_calls"`、`"role": "tool"`、`"name": "glob"` 等。

---

## 2. 工具调用如何写入 jsonl（代码路径）

```
process_direct / _process_message
  → SessionManager.get_or_create("cli:direct")
  → 提前 save 用户消息
  → AgentRunner.run() 多轮迭代
  → _save_turn(session, all_msgs, skip)
  → SessionManager.save() → cli_direct.jsonl
```

### 2.1 核心循环（`AgentRunner.run`）

每一轮：

1. 调 LLM（带 tools schema）
2. 若有 `tool_calls` → `build_assistant_message` → append 到 `messages`
3. `_execute_tools` 执行 → 构造 `role: "tool"` 消息 → append
4. 继续下一轮，直到 LLM 不再调工具

关键代码：`backend/FireFly/firefly/agent/runner.py`（约 239–287 行）

### 2.2 持久化（`_save_turn`）

- 有 `tool_calls` 的空 content assistant **会保留**
- tool 结果超过 `maxToolResultChars`（默认 16000）会截断
- 超大结果可能写入 `workspace/temp/tool_results/` 并替换为引用

关键代码：`backend/FireFly/firefly/agent/loop.py`（`_save_turn`）、`session/manager.py`（`save`）

### 2.3 文件命名

`cli:direct` → `sessions/cli_direct.jsonl`（`:` 替换为 `_`）

---

## 3. 线性 vs 并行

**不是全部线性。** 分两个层次：

### 3.1 轮次之间：严格线性

LLM 每返回一批 `tool_calls`，必须等该批**全部执行完**并写回 `messages`，才发起下一轮 LLM。jsonl 里多轮 `assistant ↔ tool` 交替即此结构。

### 3.2 同一轮内：有条件并行

主 Agent 路径 `concurrent_tools=True`（`loop.py` → `AgentRunSpec`）。

`_execute_tools` 逻辑（`runner.py`）：

- 同一 **batch** 内多个工具 → `asyncio.gather` **并行**
- **batch 之间** → `for batch` **串行**

### 3.3 分批规则（`_partition_tool_batches`）

工具可同批并行当且仅当 `concurrency_safe == True`：

```python
concurrency_safe = read_only and not exclusive  # base.py
```

| 工具类型 | 能否与同轮其他工具并行 |
|----------|------------------------|
| `glob`、`grep`、`read_file`、`list_dir`、`zotero_search` 等只读 | ✅ |
| `write_file`、`edit_file` 等写操作 | ❌ 单独串行 |
| `shell`（`exclusive=True`） | ❌ 独占串行 |
| MCP 工具（`mcp_docx-mcp_*`，默认非 read_only） | ❌ 各单独一批，串行 |

### 3.4 示例（`cli_direct.jsonl` 查第三章标题）

| 轮次 | 工具 | 执行方式 |
|------|------|----------|
| 1 | `glob` ×2 | 并行 |
| 2 | `glob` ×4 | 并行 |
| 3 | `zotero_search` | 串行 |
| 4 | `list_dir` | 串行 |
| 5 | `mcp_docx-mcp_open_document` | 串行 |
| 6 | `get_headings` + `search_text` | 串行（MCP） |
| 7 | `search_text` ×2 | 串行（MCP） |

**Subagent** 未设 `concurrent_tools`，默认 `False`，同一轮内也全串行。

### 3.5 结构示意

```
LLM 第 N 轮
  ├─ glob A ─┐
  ├─ glob B ─┼─ concurrency_safe → 并行
  └─ glob C ─┘
  ├─ mcp_open_document → 单独串行
  └─ ...
       ↓ 全部完成后
LLM 第 N+1 轮
```

---

## 4. 相关配置

| 配置 | 位置 | 作用 |
|------|------|------|
| `maxToolResultChars` | `config.json` | tool 结果写入 session 前截断上限 |
| `maxToolIterations` | `config.json` | 最多工具循环轮数 |
| `sendToolHints` | `config.json` channels | UI 工具提示 |
| `show_llm_input` / `llm_input_print` | `user.json` | 终端 LLM 请求快照 |
| `--logs` | CLI | 运行时 Tool call 日志 |
