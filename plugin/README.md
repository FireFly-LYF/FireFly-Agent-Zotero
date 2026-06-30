# FireFly-Agent-Zotero 插件

Zotero 7 条目侧栏 AI 聊天插件，通过本地 Bridge 与 FireFly Agent 后端通信。

## 开发

```powershell
npm install
npm start      # 热重载开发
npm run build  # 生产构建 XPI
```

环境要求：Zotero 7 beta、Node.js LTS。复制 `.env.example` 为 `.env` 并填入 Zotero 路径。

## 主要模块

| 文件 | 职责 |
|------|------|
| `src/modules/itemPaneLLMUI.ts` | 聊天 UI、KaTeX、多标签会话 |
| `src/modules/fireflyBridge.ts` | Bridge HTTP/SSE 通信 |
| `src/modules/wikiPdfSync.ts` | PDF → llm-wiki 同步 |
| `src/modules/preferenceScript.ts` | 设置页（config 编辑） |

## 文档

项目总览与架构见仓库根目录 [README.md](../README.md)。

基于 [Zotero Plugin Template](https://github.com/windingwind/zotero-plugin-template) 构建。
