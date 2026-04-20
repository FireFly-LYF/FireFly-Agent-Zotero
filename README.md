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
