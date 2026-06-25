"""Shell 执行工具。"""

import asyncio
import os
import re
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any

from loguru import logger

from firefly.agent.tools.base import Tool, tool_parameters
from firefly.agent.tools.sandbox import wrap_command
from firefly.agent.tools.schema import IntegerSchema, StringSchema, tool_parameters_schema
from firefly.agent.temp_workspace import get_exec_cache_dir, remove_agent_temp_file
from firefly.config.paths import get_media_dir

_IS_WINDOWS = sys.platform == "win32"
_PYTHON_C_RE = re.compile(
    r"^(?P<exe>python(?:3(?:\.\d+)?)?|py)\s+-c\s+(?P<code>.+)$",
    re.IGNORECASE | re.DOTALL,
)
_WIN_QUOTED = r'(?:"([^"]*)"|\'([^\']*)\'|(\S+))'


def _first_group(match: re.Match[str]) -> str:
    return next(group for group in match.groups() if group is not None)


def _powershell_single_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _unwrap_shell_string(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        inner = text[1:-1]
        return inner.replace('\\"', '"').replace("\\'", "'").replace("\\\\", "\\")
    return text


def _rewrite_windows_mkdir(command: str) -> str | None:
    cmd = command.strip()
    mkdir_match = re.match(rf"^mkdir\s+(?:-p\s+)?{_WIN_QUOTED}\s*$", cmd, re.IGNORECASE)
    if mkdir_match:
        path = _first_group(mkdir_match)
        ps_path = _powershell_single_quote(path)
        return (
            "powershell -NoProfile -NonInteractive -Command "
            f"New-Item -ItemType Directory -Force -Path {ps_path}"
        )

    if_not_exist = re.match(
        rf"^if\s+not\s+exist\s+{_WIN_QUOTED}\s+mkdir\s+{_WIN_QUOTED}\s*$",
        cmd,
        re.IGNORECASE,
    )
    if if_not_exist:
        path = _first_group(if_not_exist)
        ps_path = _powershell_single_quote(path)
        return (
            "powershell -NoProfile -NonInteractive -Command "
            f"if (-not (Test-Path -LiteralPath {ps_path})) "
            f"{{ New-Item -ItemType Directory -Force -Path {ps_path} }}"
        )
    return None


def _rewrite_windows_type_findstr(command: str) -> str | None:
    cmd = command.strip()
    type_findstr = re.match(
        rf"^(?:chcp\s+\d+\s*&&\s*)?type\s+{_WIN_QUOTED}\s*\|\s*findstr(.+)$",
        cmd,
        re.IGNORECASE,
    )
    if not type_findstr:
        return None
    path = _first_group(type_findstr)
    findstr_args = type_findstr.group(4).strip()
    ps_path = _powershell_single_quote(path)
    return (
        "powershell -NoProfile -NonInteractive -Command "
        f"Get-Content -LiteralPath {ps_path} -Encoding UTF8 | Select-String {findstr_args}"
    )


def preprocess_windows_command(command: str) -> str:
    """Rewrite fragile cmd.exe patterns to PowerShell equivalents."""
    for rewriter in (_rewrite_windows_mkdir, _rewrite_windows_type_findstr):
        rewritten = rewriter(command)
        if rewritten:
            return rewritten
    return command


def _write_python_script(code: str, workspace: Path) -> Path:
    root = get_exec_cache_dir(workspace)
    script = root / f"run_{uuid.uuid4().hex}.py"
    script.write_text(code, encoding="utf-8")
    return script


def rewrite_python_c_command(command: str, workspace: Path) -> tuple[str, Path | None]:
    """Run python -c via a temp script to avoid Windows/cmd quoting failures."""
    match = _PYTHON_C_RE.match(command.strip())
    if not match:
        return command, None
    exe = match.group("exe")
    code = _unwrap_shell_string(match.group("code"))
    if not code.strip():
        return command, None
    script = _write_python_script(code, workspace)
    return f'{exe} "{script}"', script


@tool_parameters(
    tool_parameters_schema(
        command=StringSchema("The shell command to execute"),
        working_dir=StringSchema("Optional working directory for the command"),
        timeout=IntegerSchema(
            60,
            description=(
                "Timeout in seconds. Increase for long-running commands "
                "like compilation or installation (default 60, max 600)."
            ),
            minimum=1,
            maximum=600,
        ),
        required=["command"],
    )
)
class ExecTool(Tool):
    """执行 shell 命令的工具。"""

    def __init__(
        self,
        timeout: int = 60,
        working_dir: str | None = None,
        deny_patterns: list[str] | None = None,
        allow_patterns: list[str] | None = None,
        restrict_to_workspace: bool = False,
        sandbox: str = "",
        path_append: str = "",
        allowed_env_keys: list[str] | None = None,
    ):
        self.timeout = timeout
        self.working_dir = working_dir
        self.sandbox = sandbox
        self.deny_patterns = deny_patterns or [
            r"\brm\s+-[rf]{1,2}\b",          # rm -r, rm -rf, rm -fr
            r"\bdel\s+/[fq]\b",              # del /f, del /q
            r"\brmdir\s+/s\b",               # rmdir /s
            r"(?:^|[;&|]\s*)format\b",       # format (as standalone command only)
            r"\b(mkfs|diskpart)\b",          # disk operations
            r"\bdd\s+if=",                   # dd
            r">\s*/dev/sd",                  # write to disk
            r"\b(shutdown|reboot|poweroff)\b",  # system power
            r":\(\)\s*\{.*\};\s*:",          # fork bomb
            # 禁止写入 firefly 内部状态文件（#2989）。
            # history.jsonl / .dream_cursor 由 append_history() 维护；
            # 直接写入会破坏游标格式并导致 /dream 崩溃。
            r">>?\s*\S*(?:history\.jsonl|\.dream_cursor)",            # > / >> redirect
            r"\btee\b[^|;&<>]*(?:history\.jsonl|\.dream_cursor)",     # tee / tee -a
            r"\b(?:cp|mv)\b(?:\s+[^\s|;&<>]+)+\s+\S*(?:history\.jsonl|\.dream_cursor)",  # cp/mv target
            r"\bdd\b[^|;&<>]*\bof=\S*(?:history\.jsonl|\.dream_cursor)",  # dd of=
            r"\bsed\s+-i[^|;&<>]*(?:history\.jsonl|\.dream_cursor)",  # sed -i
        ]
        self.allow_patterns = allow_patterns or []
        self.restrict_to_workspace = restrict_to_workspace
        self.path_append = path_append
        self.allowed_env_keys = allowed_env_keys or []

    @property
    def name(self) -> str:
        return "exec"

    _MAX_TIMEOUT = 600
    _MAX_OUTPUT = 10_000

    @property
    def description(self) -> str:
        return (
            "Execute a shell command and return its output. "
            "Prefer read_file/write_file/edit_file over cat/echo/sed, "
            "and grep/glob over shell find/grep. "
            "On Windows, mkdir/type/findstr and python -c are rewritten automatically. "
            "For multi-line Python, write scripts under temp/ then exec them. "
            "Use -y or --yes flags to avoid interactive prompts. "
            "Output is truncated at 10 000 chars; timeout defaults to 60s."
        )

    @property
    def exclusive(self) -> bool:
        return True

    async def execute(
        self, command: str, working_dir: str | None = None,
        timeout: int | None = None, **kwargs: Any,
    ) -> str:
        cwd = working_dir or self.working_dir or os.getcwd()

        # 防止 LLM 提供的 working_dir 逃逸出已配置的
        # 工作区边界（restrict_to_workspace 开启时，#2826）。若不限制
        # 调用方可传 working_dir="/etc"，随后所有位于 /etc 下的绝对路径
        # 都会绕过基于 cwd 的 _guard_command 检查
        # 。
        if self.restrict_to_workspace and self.working_dir:
            try:
                requested = Path(cwd).expanduser().resolve()
                workspace_root = Path(self.working_dir).expanduser().resolve()
            except Exception:
                return "Error: working_dir could not be resolved"
            if requested != workspace_root and workspace_root not in requested.parents:
                return "Error: working_dir is outside the configured workspace"

        guard_error = self._guard_command(command, cwd)
        if guard_error:
            return guard_error

        temp_script: Path | None = None
        if _IS_WINDOWS:
            command = preprocess_windows_command(command)
            command, temp_script = rewrite_python_c_command(command, Path(cwd))

        if self.sandbox:
            if _IS_WINDOWS:
                logger.warning(
                    "Sandbox '{}' is not supported on Windows; running unsandboxed",
                    self.sandbox,
                )
            else:
                workspace = self.working_dir or cwd
                command = wrap_command(self.sandbox, command, workspace, cwd)
                cwd = str(Path(workspace).resolve())

        effective_timeout = min(timeout or self.timeout, self._MAX_TIMEOUT)
        env = self._build_env()

        if self.path_append:
            if _IS_WINDOWS:
                env["PATH"] = env.get("PATH", "") + ";" + self.path_append
            else:
                command = f'export PATH="$PATH:{self.path_append}"; {command}'

        try:
            try:
                process = await self._spawn(command, cwd, env)

                try:
                    stdout, stderr = await asyncio.wait_for(
                        process.communicate(),
                        timeout=effective_timeout,
                    )
                except asyncio.TimeoutError:
                    await self._kill_process(process)
                    return f"Error: Command timed out after {effective_timeout} seconds"
                except asyncio.CancelledError:
                    await self._kill_process(process)
                    raise

                output_parts = []

                if stdout:
                    output_parts.append(stdout.decode("utf-8", errors="replace"))

                if stderr:
                    stderr_text = stderr.decode("utf-8", errors="replace")
                    if stderr_text.strip():
                        output_parts.append(f"STDERR:\n{stderr_text}")

                output_parts.append(f"\nExit code: {process.returncode}")

                result = "\n".join(output_parts) if output_parts else "(no output)"

                max_len = self._MAX_OUTPUT
                if len(result) > max_len:
                    half = max_len // 2
                    result = (
                        result[:half]
                        + f"\n\n... ({len(result) - max_len:,} chars truncated) ...\n\n"
                        + result[-half:]
                    )

                return result

            except Exception as e:
                return f"Error executing command: {str(e)}"
        finally:
            if temp_script is not None:
                remove_agent_temp_file(temp_script)

    @staticmethod
    async def _spawn(
        command: str, cwd: str, env: dict[str, str],
    ) -> asyncio.subprocess.Process:
        """在适配当前平台的 shell 中启动 *command*。"""
        if _IS_WINDOWS:
            comspec = env.get("COMSPEC", os.environ.get("COMSPEC", "cmd.exe"))
            return await asyncio.create_subprocess_exec(
                comspec, "/c", command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )
        bash = shutil.which("bash") or "/bin/bash"
        return await asyncio.create_subprocess_exec(
            bash, "-l", "-c", command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
        )

    @staticmethod
    async def _kill_process(process: asyncio.subprocess.Process) -> None:
        """终止子进程并回收，避免僵尸进程。"""
        process.kill()
        try:
            await asyncio.wait_for(process.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            pass
        finally:
            if not _IS_WINDOWS:
                try:
                    os.waitpid(process.pid, os.WNOHANG)
                except (ProcessLookupError, ChildProcessError) as e:
                    logger.debug("Process already reaped or not found: {}", e)

    def _build_env(self) -> dict[str, str]:
        """为子进程执行构建最小环境变量。

        On Unix, only HOME/LANG/TERM are passed; ``bash -l`` sources the
        user's profile which sets PATH and other essentials.

        On Windows, ``cmd.exe`` has no login-profile mechanism, so a curated
        set of system variables (including PATH) is forwarded.  API keys and
        other secrets are still excluded.
        """
        if _IS_WINDOWS:
            sr = os.environ.get("SYSTEMROOT", r"C:\Windows")
            env = {
                "SYSTEMROOT": sr,
                "COMSPEC": os.environ.get("COMSPEC", f"{sr}\\system32\\cmd.exe"),
                "USERPROFILE": os.environ.get("USERPROFILE", ""),
                "HOMEDRIVE": os.environ.get("HOMEDRIVE", "C:"),
                "HOMEPATH": os.environ.get("HOMEPATH", "\\"),
                "TEMP": os.environ.get("TEMP", f"{sr}\\Temp"),
                "TMP": os.environ.get("TMP", f"{sr}\\Temp"),
                "PATHEXT": os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD"),
                "PATH": os.environ.get("PATH", f"{sr}\\system32;{sr}"),
                "APPDATA": os.environ.get("APPDATA", ""),
                "LOCALAPPDATA": os.environ.get("LOCALAPPDATA", ""),
                "ProgramData": os.environ.get("ProgramData", ""),
                "ProgramFiles": os.environ.get("ProgramFiles", ""),
                "ProgramFiles(x86)": os.environ.get("ProgramFiles(x86)", ""),
                "ProgramW6432": os.environ.get("ProgramW6432", ""),
            }
            for key in self.allowed_env_keys:
                val = os.environ.get(key)
                if val is not None:
                    env[key] = val
            env.setdefault("PYTHONIOENCODING", "utf-8")
            return env
        home = os.environ.get("HOME", "/tmp")
        env = {
            "HOME": home,
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "TERM": os.environ.get("TERM", "dumb"),
        }
        for key in self.allowed_env_keys:
            val = os.environ.get(key)
            if val is not None:
                env[key] = val
        return env

    def _guard_command(self, command: str, cwd: str) -> str | None:
        """针对潜在破坏性命令的尽力安全防护。"""
        cmd = command.strip()
        lower = cmd.lower()

        for pattern in self.deny_patterns:
            if re.search(pattern, lower):
                return "Error: Command blocked by safety guard (dangerous pattern detected)"

        if self.allow_patterns:
            if not any(re.search(p, lower) for p in self.allow_patterns):
                return "Error: Command blocked by safety guard (not in allowlist)"

        from firefly.security.network import contains_internal_url
        if contains_internal_url(cmd):
            return "Error: Command blocked by safety guard (internal/private URL detected)"

        if self.restrict_to_workspace:
            if "..\\" in cmd or "../" in cmd:
                return "Error: Command blocked by safety guard (path traversal detected)"

            cwd_path = Path(cwd).resolve()

            for raw in self._extract_absolute_paths(cmd):
                try:
                    expanded = os.path.expandvars(raw.strip())
                    p = Path(expanded).expanduser().resolve()
                except Exception:
                    continue

                media_path = get_media_dir().resolve()
                if (p.is_absolute() 
                    and cwd_path not in p.parents 
                    and p != cwd_path
                    and media_path not in p.parents
                    and p != media_path
                ):
                    return "Error: Command blocked by safety guard (path outside working dir)"

        return None

    @staticmethod
    def _extract_absolute_paths(command: str) -> list[str]:
        # Windows：匹配 `C:\` 这类盘符根路径，也匹配 `C:\path\to\file`
        # 注意：这里需要 `*`，否则 `C:\`（斜杠后无内容）无法被提取。
        win_paths = re.findall(r"[A-Za-z]:\\[^\s\"'|><;]*", command)
        posix_paths = re.findall(r"(?:^|[\s|>'\"])(/[^\s\"'>;|<]+)", command) # POSIX: /absolute only
        home_paths = re.findall(r"(?:^|[\s|>'\"])(~[^\s\"'>;|<]*)", command) # POSIX/Windows home shortcut: ~
        return win_paths + posix_paths + home_paths
