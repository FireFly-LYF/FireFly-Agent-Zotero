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
