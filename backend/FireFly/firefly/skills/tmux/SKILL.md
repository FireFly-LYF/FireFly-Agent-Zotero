---
name: tmux
description: 通过发送按键与抓取窗格输出来远程控制 tmux 会话（适合交互式 CLI）。
metadata: {"firefly":{"emoji":"🧵","os":["darwin","linux"],"requires":{"bins":["tmux"]}}}
---

# 多路复用技巧

仅当您需要交互式 TTY 时才使用 tmux。对于长时间运行的非交互式任务，首选 exec 后台模式。

## 快速入门（隔离套接字、执行工具）

```bash
SOCKET_DIR="${FIREFLY_TMUX_SOCKET_DIR:-${TMPDIR:-/tmp}/firefly-tmux-sockets}"
mkdir -p "$SOCKET_DIR"
SOCKET="$SOCKET_DIR/firefly.sock"
SESSION=firefly-python

tmux -S "$SOCKET" new -d -s "$SESSION" -n shell
tmux -S "$SOCKET" send-keys -t "$SESSION":0.0 -- 'PYTHON_BASIC_REPL=1 python3 -q' Enter
tmux -S "$SOCKET" capture-pane -p -J -t "$SESSION":0.0 -S -200
```

启动会话后，始终打印监视器命令：

```
To monitor:
  tmux -S "$SOCKET" attach -t "$SESSION"
  tmux -S "$SOCKET" capture-pane -p -J -t "$SESSION":0.0 -S -200
```

## 套接字约定

- 使用 `FIREFLY_TMUX_SOCKET_DIR` 环境变量。
- 默认套接字路径：`"$FIREFLY_TMUX_SOCKET_DIR/firefly.sock"`。

## 定位窗格和命名

- 目标格式：`session:window.pane`（默认为 `:0.0`）。
- 名字要简短；避免空格。
- 检查：`tmux -S "$SOCKET" list-sessions`、`tmux -S "$SOCKET" list-panes -a`。

## 查找会话

- 列出套接字上的会话：`{baseDir}/scripts/find-sessions.sh -S "$SOCKET"`。
- 扫描所有套接字：`{baseDir}/scripts/find-sessions.sh --all`（使用`FIREFLY_TMUX_SOCKET_DIR`）。

## 安全发送输入

- 首选文字发送：`tmux -S "$SOCKET" send-keys -t target -l -- "$cmd"`。
- 控制键：`tmux -S "$SOCKET" send-keys -t target C-c`。

## 观察输出

- 捕获最近的历史记录：`tmux -S "$SOCKET" capture-pane -p -J -t target -S -200`。
- 等待提示：`{baseDir}/scripts/wait-for-text.sh -t session:0.0 -p 'pattern'`。
- 粘贴就OK了；使用 `Ctrl+b d` 分离。

## 产卵过程

- 对于 python REPL，设置 `PYTHON_BASIC_REPL=1` （非基本 REPL 会破坏发送密钥流）。

## Windows / WSL

- macOS/Linux 支持 tmux。在 Windows 上，使用 WSL 并在 WSL 内安装 tmux。
- 该技能被限制在 `darwin`/`linux` 上，并且需要 PATH 上的 `tmux` 。

## 编排编码代理（Codex、Claude Code）

tmux 擅长并行运行多个编码代理：

```bash
SOCKET="${TMPDIR:-/tmp}/codex-army.sock"

# Create multiple sessions
for i in 1 2 3 4 5; do
  tmux -S "$SOCKET" new-session -d -s "agent-$i"
done

# Launch agents in different workdirs
tmux -S "$SOCKET" send-keys -t agent-1 "cd /tmp/project1 && codex --yolo 'Fix bug X'" Enter
tmux -S "$SOCKET" send-keys -t agent-2 "cd /tmp/project2 && codex --yolo 'Fix bug Y'" Enter

# Poll for completion (check if prompt returned)
for sess in agent-1 agent-2; do
  if tmux -S "$SOCKET" capture-pane -p -t "$sess" -S -3 | grep -q "❯"; then
    echo "$sess: DONE"
  else
    echo "$sess: Running..."
  fi
done

# Get full output from completed session
tmux -S "$SOCKET" capture-pane -p -t agent-1 -S -500
```

**尖端：**
- 使用单独的 git 工作树进行并行修复（无分支冲突）
- 在新克隆中运行 codex 之前先执行 `pnpm install`
- 检查 shell 提示符（`❯` 或 `$`）以检测完成情况
- Codex 需要 `--yolo` 或 `--full-auto` 进行非交互式修复

## 清理

- 终止会话：`tmux -S "$SOCKET" kill-session -t "$SESSION"`。
- 终止套接字上的所有会话：`tmux -S "$SOCKET" list-sessions -F '#{session_name}' | xargs -r -n1 tmux -S "$SOCKET" kill-session -t`。
- 删除私有套接字上的所有内容：`tmux -S "$SOCKET" kill-server`。

## 助手：wait-for-text.sh

`{baseDir}/scripts/wait-for-text.sh` 轮询窗格中是否有超时的正则表达式（或固定字符串）。

```bash
{baseDir}/scripts/wait-for-text.sh -t session:0.0 -p 'pattern' [-F] [-T 20] [-i 0.5] [-l 2000]
```

- `-t`/`--target` 窗格目标（必需）
- `-p`/`--pattern` 正则表达式来匹配（必需）；为固定字符串添加 `-F`
- `-T` 超时秒数（整数，默认 15）
- `-i` 轮询间隔秒（默认 0.5）
- `-l` 要搜索的历史行（整数，默认 1000）
