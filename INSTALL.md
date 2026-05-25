# FireFly-Agent-Zotero 安装指南（Windows 一键包）

本仓库采用 **Zotero 插件（XPI）+ 本地 Python 后端**。GitHub Release 提供 **单个 ZIP**，解压即可安装。

## 系统要求

| 组件 | 要求 |
|------|------|
| 操作系统 | **Windows 10/11** |
| Zotero | 7.x |
| Python | 3.11+（安装时勾选 “Add to PATH”） |
| 网络 | 调用 LLM API；Bridge 仅监听 `127.0.0.1:8765` |

## 下载

在 [GitHub Releases](https://github.com/FireFly-LYF/FireFly-agent-zotero/releases) 打开对应版本。

> **只下载** Assets 中的 **`FireFly-Agent-Zotero-vX.Y.Z-windows.zip`**。  
> **不要**点页面顶部的 **Source code (zip)**（那是整仓源码，不是安装包）。

## 安装步骤

### 1. 解压 ZIP

解压到任意目录，例如 `D:\FireFly-Agent-Zotero-bundle\`。

解压后目录示例：

```text
FireFly-Agent-Zotero-3.7.30/
  QUICKSTART.txt
  INSTALL.md
  install-backend.ps1
  start-bridge.ps1
  plugin/
    fire-fly-agent-zotero.xpi
  backend/
    firefly_ai-0.1.5-py3-none-any.whl
  llm-wiki/
    AGENTS.md
    raw/ ...
    wiki/ ...
```

### 2. 安装后端

在解压目录打开 **PowerShell**：

```powershell
.\install-backend.ps1
```

可选自定义安装位置：

```powershell
.\install-backend.ps1 -InstallDir "D:\FireFly-Agent-Zotero"
```

默认安装到 `%USERPROFILE%\FireFly-Agent-Zotero`（含 `venv`、`config`、`workspace`、`llm-wiki`）。

### 3. 配置 API Key

编辑 `%USERPROFILE%\FireFly-Agent-Zotero\config\config.json`，在 `providers` 中填入 LLM API Key。

也可在 Zotero **设置 → FireFly-Agent-Zotero** 中编辑（需 Bridge 已启动）。

### 4. 启动 Bridge

在**解压目录**执行（不要关窗口）：

```powershell
.\start-bridge.ps1
```

若安装到了自定义目录：

```powershell
.\start-bridge.ps1 -InstallDir "D:\FireFly-Agent-Zotero"
```

看到 `Zotero bridge listening on http://127.0.0.1:8765/...` 即成功。

### 5. 安装 Zotero 插件

1. Zotero → **工具 → 插件**
2. **从文件安装插件…**
3. 选择解压包内 `plugin\fire-fly-agent-zotero.xpi`
4. 重启 Zotero

## 开发者（源码仓库）

```powershell
cd backend\FireFly
python -m venv .venv
.\.venv\Scripts\activate
pip install -e ".[api,pdf]"
firefly onboard -c ..\config\config.json -w ..\workspace
python scripts\zotero_bridge_launcher.py

cd ..\..\plugin
npm install
npm run build
```

## 发布新版本（维护者）

```powershell
cd plugin
npm run release
```

推送 `v*` 标签后，CI 会生成并上传 **`FireFly-Agent-Zotero-vX.Y.Z-windows.zip`** 单一安装包。

## 常见问题

**Release 里只有 Source code，没有 ZIP 包**

- Actions 可能失败或未重跑；到 **Actions → Release** 手动 Run workflow，或发新版本 tag。

**插件提示 Bridge 未连接**

- 确认 `start-bridge.ps1` 窗口仍在运行
- 浏览器打开 `http://127.0.0.1:8765/health` 应返回 `{"status":"ok"}`

**修改 config.json 后不生效**

- 需重启 Bridge
