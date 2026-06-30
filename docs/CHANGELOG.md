# 开发日志

按时间倒序记录主要里程碑，完整技术细节见 Git 历史。

## 2026.6.29

1. `rag_search` 移除父类大量召回，避免无关内容截断后反复搜索
2. 修复未使用 `plan_tasks` 时主 Agent 连续 spawn 十多个子任务的 bug
3. Agent 自行判断编排/直接模式；移除 runtime 字符匹配强制路由
4. `Summarizer` 类封装回合总结；docx 路径统一至 `backend/llm-wiki/docx/`
5. 修复 thinking 首行缩进展示

## 2026.6.28

1. 模型切换为 glm-4.5-air，精简 prompt 下可一次召回多个 tool calls
2. 架构调整：主 Agent 编排 + Subagent 执行，上下文大幅减少
3. **issue**：Subagent 多次重复阅读 md，定位效率低

## 2026.6.27

1. Summarizer 整轮任务结束后只调用一次，无工具时直接流式输出
2. 工作流明确 `get_headings` + `search_text` 等只读操作应同轮并行
3. 修复 `search_text` `regex=false` 字串匹配时 `|` 导致空结果

## 2026.6.26

1. markdown 用 grep 定位，不要凭 offset 猜
2. 用户指定路径时用同一路径覆盖 docx
3. 带 tool_calls 的超长 assistant 草稿不写入用户可见历史
4. 写 docx 后 read_file 检查是否真有空白段落

## 2026.6.25

1. 阅读 tool_calls 与 agent loop 核心循环，分析并行/串行调用
2. 新增 docx-mcp 工具
3. RAG 改为 Agent 自主调用的工具，优先 markdown 而非 pdf
4. **issue**：多轮工具调用未展示，Agent 运行时间较长

## 2026.5.25

1. 插件设置页可编辑 `backend/config` 三份 JSON
2. GitHub Release 一体包：XPI + wheel + llm-wiki 模板 + 安装脚本

## 2026.5.11

1. 完全重置公式渲染逻辑
2. 新增删除、重置按钮
3. 可通过 markdown 撰写 wiki，提问时组装为上下文

## 2026.5.7

1. 修复公式显示 bug
2. llm-wiki 目录：每篇文章一个 wiki + 共用 `_synthesis`
3. 修复 markdown→rag 切分不严格按标题号的问题

## 2026.4.23

1. nanobot 完全重构为 FireFly
2. 新增意图识别改善 RAG 检索逻辑
3. 前端增加 KaTeX，长公式自动换行

## 2026.4.22

1. 添加 LLM wiki 文件夹
2. 增加 markdown skill：pdf→markdown、markdown→rag chunks
3. 实现 RAG 链路

## 2026.4.20

1. 优化 Zotero 页面，新增标签页、文字截取工具
2. 截图工具占位

## 2026.4.16

1. 完成 nanobot 框架阅读，新建 zotero 命令
2. 建立 zotero-nanobot 链路

## 2026.4.14

1. 前端为 Zotero 官方插件模板，添加 LLM 聊天窗口
2. 后端为 nanobot 轻量级 agent
