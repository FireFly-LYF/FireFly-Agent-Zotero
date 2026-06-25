# ISSUE 备忘

---

## 一、Zotero 本地 RAG：召回与注入

### 背景

FireFly 通过 Zotero 插件把当前 PDF 对应到 `llm-wiki/raw/pdf/...`，再映射到 `raw/rag/...jsonl`，在用户提问里注入 `[RAG Context] ... [/RAG Context]`。2026-05 前后修过多轮检索与注入逻辑；以下为现象、原因与涉及文件，便于回归。

### 现象摘要

- 问「4.2 干扰有效性验证」一类小节问题时，召回跑到篇首、第 1 章或摘要。
- 有一段时间接口侧完全没有 `[RAG Context]`，但本地 JSONL 里明明有块。
- 日志或快照里能看见检索正文，模型回答仍像只读了摘要，不沾小节实验表述。
- 开启 `user.json` 里 `llm_input_print` 分段打印时，`rag` 段可能出现省略号占位或多段连在一起，容易误以为注入了两次。

### 原因与对策（按模块）

检索排序（`rag_utils.py`）

- 小节标题带 Markdown 加粗（例如节号两边星号），旧逻辑按「段首纯数字」解析会失败，章节号得分接近零。后来在去掉噪声后再解析编号，并把章节号匹配、标题是否与问题重合等纳入初筛和最终排序。
- 只靠正文分词时，整句中文 token 很难和正文逐字重合；需要同时用语义章节号（如 4.2）和用户问题里的编号一起做加权。
- 章节号提取曾漏掉单独的「4」；后来又出现过英文标识符里的数字「1」被当成第一节。处理方式包括：数字边界、改进分词、章节号与子串用「整段匹配」规则避免 1 误贴到 4.1 上。
- 路径里已有「文档 › D › 4.1」这类链时，曾误把第一级 `D` 当成整章前缀，把全文扩进结果；改为路径中已出现数字小节时，不再把第一级当这种文本章前缀。
- 两路都没命中时，用按 `chunk_index` 取前若干块作兜底，避免完全空结果（同时注意兜底与误召之间的平衡）。

路径与文件（`commands.py` 中解析函数）

- 用字符串把 `head` 和 `raw/rag` 硬拼时，若少了一个路径分隔符，会拼出无效路径，文件永远判不存在，检索直接返回空。已改为用 `Path` 逐段拼接，并在主路径不存在时，在 `raw/rag` 下按 PDF 主文件名做唯一 `jsonl` 的查找，并打日志。

是否注入（`commands.py` 中 `_should_inject_rag`）

- 过去只有出现「论文、实验、本节」等词才注入；像「分析 4.2 节」可能一条都不中，于是一轮里完全没有 RAG 块。现约定：只要消息里带有插件写的 `zotero_current_wiki_pdf_path` 当前文献标记（并排除极短闲聊），就默认做 RAG 注入。

模型仍写套话（`identity.md` 与注入文案）

- 系统里「先查工具」等规则与「已经把段落塞进用户消息」并存时，模型仍可能按摘要式套路写。处理：在 Zotero 渠道的 identity 里增加「本地 RAG 段落为事实主依据」的说明；在注入块末尾和用户消息末尾增加简短再次确认句，减轻「只顾末尾不问前文」的情况。

分段打印误解（`llm_display_compose.py`）

- 分段视图里的 `rag` 是从本轮请求里所有 message 中抽取的全部 `[RAG Context]` 拼起来的。历史轮次若也含 RAG（或被脱敏成省略号），看起来像两段；不等于本轮 API 又多发了一遍。

### 相关路径一览

| 用途 | 文件 |
|------|------|
| 召回与排序 | `backend/FireFly/firefly/skills/markdown/scripts/rag_utils.py` |
| PDF 映射 jsonl、注入条件、提示句 | `backend/FireFly/firefly/zotero_interface/commands.py` |
| Zotero 系统提示补充 | `backend/FireFly/firefly/templates/agent/identity.md` |
| 仅用于终端/调试的分段展示 | `backend/FireFly/firefly/utils/llm_display_compose.py` |
| 用户侧开关与 RAG 参数示例 | `backend/config/user.json` |

### 测试

`backend/FireFly/tests/zotero_interface/test_rag_chapter_expand.py` 覆盖编号路径、扩展、4.2 优先、fallback、章节号提取等。

### 后续可做（未全部实现）

- 分段展示只从最后一条 user 抽 RAG，避免历史混在 `sections.rag` 里。
- 仅问某节时，在排序或过滤上更狠地压掉无关章（如第 1 章）的块。
- 若模型仍不跟片段：调温度、换模型、或减少单轮注入块数，减轻注意力被冲散。

以当前仓库代码为准；与本文不一致时以代码行为优先。

---

## 二、插件里冒号与行内公式（`renderMarkdownLite`）

### 现象

助手气泡中，若列表行里既有半角或全角冒号，又有行内公式 `$...$`，有时公式会原样显示为美元符号，而独立成行的块公式往往仍正常。

### 原因

列表行会走 `enhanceFormulaSegmentAfterColon`（见 `plugin/src/modules/itemPaneLLMUI.ts`）。早期用正则限制「从行首到第一个冒号之间最长若干字符」才认定冒号；正文一长、第一个冒号出现在「如下：」等靠后位置时，整行匹配失败，退回只做 HTML 转义，不再走行内 Markdown/KaTeX，于是所有 `$...$` 都不会被当公式。本质是冒号前长度上限导致未进入公式分支，而不是冒号破坏了 LaTeX。

### 修复思路（已实现）

按第一个半角或全角冒号切开前缀与后缀，去掉「冒号前至多 N 字」一类限制；该行若没有冒号，则直接走行内渲染。

### 代码位置

`plugin/src/modules/itemPaneLLMUI.ts`：`enhanceFormulaSegmentAfterColon`，以及 `renderMarkdownLite` 里处理列表项的分支。
