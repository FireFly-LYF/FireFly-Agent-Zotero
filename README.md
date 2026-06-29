# 开发日志

## 2026.4.14

1. 前端为 Zotero 官方插件模板，已添加 LLM 聊天窗口，功能均未实现
2. 后端为 nanobot 轻量级 agent，下一步查看其 LLM 调用链路

## 2026.4.16

1. 完成 nanobot 框架阅读，新建 nanobot zotero 命令，独立于 nanobot agent
2. 建立 zotero-nanobot 链路，现在可在 Zotero 里接收到 LLM 回复
3. 考虑在 Zotero 端单独添加一个消息通道，由 Zotero 向 agent 传递文献信息

## 2026.4.20

1. 优化 Zotero 页面，新增标签页、文字截取工具已完成
2. 截图工具仅占位，需要接入外部工具
3. 考虑查看整个发送给 LLM 的上下文，确保截取的文字和图片被清晰描述
4. 考虑将 nanobot 重构为 FireFly，且添加多个矢量图

## 2026.4.22

1. 添加 LLM wiki 文件夹，含 raw（原始材料）、wiki（LLM 维护）、md 文件
2. 增加 markdown skill，可实现 pdf→markdown、markdown→rag chunks
3. 实现 RAG 链路，切分 raw/markdown 至 raw/rag，使用相关性粗召回
4. 当前检索仅 rag，尚未对 LLM wiki 进行维护，这是下一步的工作目标

## 2026.4.23

1. 将 nanobot 完全重构为 FireFly，修改了 onboard 和启动配置；现在会在当前路径生成 workspace 和 config，启动时会读取上次 onboard 的路径配置
2. 新增意图识别以改善 rag 检索的逻辑，避免所有时刻都进行 rag
3. 前端增加 KaTeX 库，支持公式显示；为适配窄侧边栏，对长公式采用自动换行策略
4. 下一步加入 LLM wiki 联合检索

## 2026.5.7

1. 修复了公式显示 bug，现在文本内的少字公式都会被正常解析
2. 修改了 llm-wiki 的目录设置，现在为每篇文章一个 wiki 与多篇共用的 `_synthesis`
3. 修复了 markdown→rag 切分时存在不严格按照标题号切分的问题，该问题导致大段文字无法召回

## 2026.5.11

1. 完全重置了公式渲染逻辑，参考 llm_for_zotero
2. 新增删除、重置按钮，可删除/重新发送历史对话
3. 现在可通过 markdown 撰写 wiki，用户提问时将 wiki 组装为上下文
4. LLM wiki 目前仅进行了单文件测试，尚未验证能否总结出多篇文章之间的联系

## 2026.5.25

1. 插件设置页可编辑 `backend/config` 三份 JSON
2. GitHub Release 一体包：XPI + firefly_ai wheel + llm-wiki 模板 + 安装脚本，见 [INSTALL.md](INSTALL.md)

## 2026.6.25

1. 阅读 tool_calls 调用逻辑，阅读 agent loop 核心循环，分析工具的并行/串行调用
2. 新增 tool：docx-mcp，实现 docx 格式文本读写
3. rag 改为 agent 自主调用的工具，优先读取 markdown 格式文件而非 pdf
4. 现有问题：Zotero 多轮工具调用未展示，Agent 运行时间较长

## 2026.6.26 issue

1. 对 markdown 用 grep 定位，不要凭 offset 猜
2. 用户指定路径时，用同一路径 `create_from_markdown` 或 docx 编辑工具覆盖
3. 考虑不把带 tool_calls 的超长 assistant 草稿写入用户可见历史，或只存摘要
4. 写 docx 后 read_file / 打开检查是否真有空白段落，再回复用户

## 2026.6.27

1. Summarizer 整轮任务结束后只调用一次，无工具时直接流式输出 agent 回复
2. 工作流中明确 `get_headings` + `search_text` 等只读操作应同轮并行
3. 修复 `search_text` `regex=false` 字串匹配时 `|` 导致空结果的问题

**issue：**

1. 模型不能够并行调用工具，导致任务时间很长
2. 前端展示非常僵硬，动态展示尚未实现
3. thinking 文本存在首行缩进不统一的问题
4. 文本样式不美观，可参考 llm_for_zotero

## 2026.6.28

1. 模型切换为 glm-4.5-air，在精简 prompt 下可一次召回多个 tool calls
2. 上下文过大导致实际使用中对可并行的工具仍是串行调用
3. 修改架构：主 agent 编排任务，派发 tool 和 skill，subagent 具体执行，上下文大幅减少，速度明显加快
4. **issue**：subagent 大量多次阅读 md，定位文章内容，耗时较长，效率极低

## 2026.6.29

1. `rag_search` 工具效果不佳，在现有模糊输入下难以召回指定内容；现已移除父类召回，防止召回大量无关内容，导致文本过多被截断后还需多次搜索
2. 主 agent 在短时间内连续 spawn 了十多个子任务，且每个子任务都在重复 `rag_search`；该 bug 为未使用 `plan_tasks` 模式引发，现已将 agent 分为编排模式和直接模式
3. Agent 自行判断启用编排模式/直接模式，但目前测试中均使用直接模式完成
4. 移除 runtime 字符匹配强制路由；`Summarizer` 类封装回合总结；docx 路径统一至 `backend/llm-wiki/docx/`；修复 thinking 首行缩进展示
