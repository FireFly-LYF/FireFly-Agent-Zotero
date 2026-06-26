开发日志

2026.4.14  
1、前端为zotero官方插件模板，已添加LLM聊天窗口，功能均未实现;  
2、后端为nanobot轻量级agent，下一步查看其LLM调用链路。

2026.4.16  
1、完成nanobot框架阅读，新建nanobot zotero命令，独立于nanobot agent;  
2、建立zotero-nanobot链路，现在可在zotero里接收到LLM回复;  
3、考虑在zotero端单独添加一个消息通道，由zotero向agent传递文献信息。

2026.4.20  
1、优化zotero页面，新增标签页、文字截取工具已完成;  
2、截图工具仅占位，需要接入外部工具;  
3、考虑查看整个发送给LLM的上下文，确保截取的文字和图片被清晰描述;  
4、考虑将nanobot重构为FireFly，且添加多个矢量图。  

2026.4.22  
1、添加LLM wiki文件夹，含raw（原始材料），wiki（LLM维护），md文件;  
2、增加markdown skill，可实现pdf->markdown, markdown->rag chunks;  
3、实现RAG链路，切分raw/markdown至raw/rag，使用相关性粗召回;  
4、当前检索仅rag，尚未对LLM wiki进行维护，这是下一步的工作目标。

2026.4.23  
1、将nanobot完全重构为FireFly，修改了onboard和启动配置，  
   现在会在当前路径生成workspace和config，启动时会读取上次onboard的路径配置;  
2、新增意图识别以改善rag检索的逻辑，避免所有时刻都进行rag;  
3、前端增加Katex库，支持公式显示，为适配窄侧边栏，对长公式采用自动换行策略;  
4、下一步加入LLM wiki联合检索。

2026.5.7  
1、修复了公式显示bug，现在文本内的少字公式都会被正常解析;   
2、修改了llm wiki的目录设置，现在为每篇文章一个wiki与多篇共用的_synthesis;   
3、修复了markdown->rag切分时，存在不严格按照标题号切分的问题，
   该问题导致大段文字无法召回。

2026.5.11   
1、完全重置了公式渲染逻辑，参考llm_for_zotero;   
2、新增删除、重置按钮，可删除/重新发送历史对话;   
3、现在可通过markdown攥写wiki，用户提问时将wiki组装为上下文;   
4、LLM wiki目前仅进行了单文件测试，尚未验证能否总结出多篇文章之间的联系。

2026.5.25  
1、插件设置页可编辑 backend/config 三份 JSON；  
2、GitHub Release 一体包：XPI + firefly_ai wheel + llm-wiki 模板 + 安装脚本，见 [INSTALL.md](INSTALL.md)。

2026.6.25
1、阅读tool_calls调用逻辑，阅读agent loop核心循环，分析工具的并行/串行调用。
2、新增tool：docx-mcp，实现docx格式文本读写;
3、rag改为agent自主调用的工具，优先读取markdown格式文件而非pdf;
4、现有问题: Zotero多轮工具调用未展示, Agent运行时间较长。

2026.6.26 issue
1、对 markdown 用 grep 定位，不要凭 offset 猜;
2、用户指定路径时，用同一路径 create_from_markdown 或 docx 编辑工具覆盖;
3、考虑不把带 tool_calls 的超长 assistant 草稿写入用户可见历史，或只存摘要;
4、写 docx 后 read_file / 打开检查是否真有空白段落，再回复用户