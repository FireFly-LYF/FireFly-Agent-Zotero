# firefly（中文说明）

这是 `firefly` 的中文化说明入口文件（精简版）。

原项目定位为轻量级个人 AI Agent，核心能力包括：

- 多模型接入（多 Provider）
- 工具调用（文件、命令、网络、MCP 等）
- 会话与记忆管理
- 多渠道接入（如 Telegram、Discord、Slack、微信相关渠道等）
- CLI / 网关 / OpenAI 兼容 API / Python SDK 多种运行形态

## 快速开始

```bash
pip install -e .
firefly onboard
firefly agent
```

## 常用命令

- `firefly onboard`：初始化配置与工作区
- `firefly agent`：启动交互式 Agent
- `firefly gateway`：启动渠道网关
- `firefly serve`：启动 OpenAI 兼容 API
- `firefly status`：查看状态

## 目录说明（简要）

- `firefly/`：核心源码
- `docs/`：文档
- `tests/`：测试
- `bridge/`：桥接服务相关
- `assets/`：图片与文档资源

## 说明

当前文件为中文精简版，便于在本项目中快速查阅。若需完整细节，请结合仓库中的其他文档与源码阅读。
