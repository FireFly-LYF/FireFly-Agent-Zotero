# FireFly-Agent-Zotero 安装指南（GitHub Release 一体包）

本仓库采用 **插件 XPI + 本地 Python 后端** 架构。Release 页会同时提供两者及安装脚本。

## 系统要求

| 组件 | 要求 |
|------|------|
| Zotero | 7.x（Beta 亦可） |
| Python | 3.11+ |
| 操作系统 | Windows / macOS / Linux |
| 网络 | 调用 LLM API；Bridge 仅监听 `127.0.0.1:8765` |

## Release 资产说明

| 文件 | 用途 |
|------|------|
| `fire-fly-agent-zotero.xpi` | Zotero 插件 |
| `firefly_ai-*-py3-none-any.whl` | FireFly 后端（Python 包） |
| `llm-wiki-template.zip` | 文献 wiki 目录骨架 |
| `install-backend.ps1` / `.sh` | 一键安装后端 |
| `start-bridge.ps1` / `.sh` | 启动 Zotero Bridge |
| `update.json` | 插件自动更新清单（由 CI 同步到 `release` 标签） |

## 用户安装（Release 下载）

### 1. 下载

在 [GitHub Releases](https://github.com/FireFly-LYF/FireFly-agent-zotero/releases) 打开对应版本。

> **重要**：不要点页面顶部 **Source code (zip)** / **Source code (tar.gz)**——那是 GitHub 自动附带的**整仓源码快照**，不是安装包。
>
> 请滚动到页面下方 **Assets（资产）** 区域，下载：

- `fire-fly-agent-zotero.xpi`
- `firefly_ai-*-py3-none-any.whl`
- `llm-wiki-template.zip`
- `install-backend.ps1`（Windows）或 `install-backend.sh`（macOS/Linux）
- `start-bridge.ps1` 或 `start-bridge.sh`

将上述文件放在**同一文件夹**。

### 2. 安装后端

**Windows（PowerShell）：**

```powershell
.\install-backend.ps1
# 自定义目录：
.\install-backend.ps1 -InstallDir "D:\FireFly-Agent-Zotero"
```

**macOS / Linux：**

```bash
chmod +x install-backend.sh start-bridge.sh
./install-backend.sh
# 自定义目录：
./install-backend.sh --install-dir ~/FireFly-Agent-Zotero
```

默认安装到：

- Windows: `%USERPROFILE%\FireFly-Agent-Zotero`
- Unix: `~/FireFly-Agent-Zotero`

目录结构：

```text
FireFly-Agent-Zotero/
  venv/                 # Python 虚拟环境
  config/
    config.json         # 主配置（含 API Key）
    context.json        # 路径锚点
    user.json           # 本地偏好
  workspace/            # Agent 工作区
  llm-wiki/             # 文献 wiki（从模板解压）
```

### 3. 配置 API Key

编辑 `config/config.json`，在 `providers` 下填入你的 LLM API Key（例如 `dashscope`、`openrouter` 等）。

也可在 Zotero **设置 → FireFly-Agent-Zotero** 中编辑（需 Bridge 已启动）。

### 4. 启动 Bridge

**Windows：**

```powershell
.\start-bridge.ps1
```

**macOS / Linux：**

```bash
./start-bridge.sh
```

看到 `Zotero bridge listening on http://127.0.0.1:8765/...` 即表示成功。请保持该终端窗口运行。

### 5. 安装 Zotero 插件

1. Zotero → **工具 → 插件**
2. 齿轮菜单 → **从文件安装插件…**
3. 选择 `fire-fly-agent-zotero.xpi`
4. 重启 Zotero

插件配置 `updateURL` 后，后续可在插件管理器中自动检查更新。

## 开发者安装（源码仓库）

```bash
# 后端
cd backend/FireFly
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[api,pdf]"

# 初始化（在仓库根目录已有 backend/config 时）
firefly onboard -c backend/config/config.json -w backend/workspace

# 启动 Bridge
python backend/FireFly/scripts/zotero_bridge_launcher.py
# 或
firefly zotero -c backend/config/config.json -w backend/workspace

# 插件
cd plugin
npm install
npm run build
# Zotero 开发模式加载 .scaffold/build/addon
```

## 发布新版本（维护者）

```bash
cd plugin
npm run release    # 选择版本号，打 tag vX.Y.Z 并 push
```

推送 tag 后，GitHub Actions（`.github/workflows/release.yml`）会自动：

1. 构建 XPI + `update.json`
2. 构建 `firefly_ai` wheel / sdist
3. 打包 `llm-wiki-template.zip`
4. 创建 GitHub Release 并上传全部资产
5. 更新 `release` 标签上的自动更新清单

**注意**：`plugin/package.json` 版本（插件）与 `backend/FireFly/pyproject.toml` 版本（后端）可独立递增；Release Notes 中会标注兼容组合。

## 常见问题

**插件提示 Bridge 未连接**

- 确认 `start-bridge` 脚本仍在运行
- 浏览器访问 `http://127.0.0.1:8765/health` 应返回 `{"status":"ok"}`

**修改 config.json 后不生效**

- 需重启 Bridge 进程

**llm-wiki 文献目录**

- PDF/Markdown 由插件同步到 `llm-wiki/raw/`；首次使用请确认该目录与 Zotero 库路径配置一致（开发模式见 `plugin/src/modules/wikiPdfSync.ts`）
