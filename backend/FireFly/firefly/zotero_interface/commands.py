"""firefly 的zotero接口命令集合。"""

import asyncio
import json
import os
import re
import select
import signal
import sys
import time
import uuid
from contextlib import nullcontext
from pathlib import Path
from typing import Any

# 在 Windows 控制台强制使用 UTF-8 编码
if sys.platform == "win32":
    if sys.stdout.encoding != "utf-8":
        os.environ["PYTHONIOENCODING"] = "utf-8"
        # 以 UTF-8 编码重新打开 stdout/stderr
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

import typer
from loguru import logger
from prompt_toolkit import PromptSession, print_formatted_text
from prompt_toolkit.application import run_in_terminal
from prompt_toolkit.formatted_text import ANSI, HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table
from rich.text import Text

from firefly import __logo__, __version__


class SafeFileHistory(FileHistory):
    """写入历史记录时清理代理字符（surrogate）的 FileHistory 子类。

    在 Windows 上，某些 Unicode 输入（例如 emoji、混合脚本）可能产生
    surrogate 字符，导致 prompt_toolkit 写文件时崩溃。
    参见 issue #2846。
    """

    def store_string(self, string: str) -> None:
        safe = string.encode("utf-8", errors="surrogateescape").decode("utf-8", errors="replace")
        super().store_string(safe)
from firefly.cli.stream import StreamRenderer, ThinkingSpinner
from firefly.config.paths import get_workspace_path, is_default_workspace
from firefly.config.schema import Config
from firefly.utils.helpers import sync_workspace_templates
from firefly.utils.restart import (
    consume_restart_notice_from_env,
    format_restart_completed_message,
    should_show_cli_restart_notice,
)

app = typer.Typer(
    name="firefly",
    context_settings={"help_option_names": ["-h", "--help"]},
    help=f"{__logo__} - Personal AI Assistant",
    no_args_is_help=True,
)

console = Console()
EXIT_COMMANDS = {"exit", "quit", "/exit", "/quit", ":q"}

# ---------------------------------------------------------------------------
# CLI 输入：使用 prompt_toolkit 提供编辑、粘贴、历史记录与显示能力
# ---------------------------------------------------------------------------

_PROMPT_SESSION: PromptSession | None = None
_SAVED_TERM_ATTRS = None  # 原始 termios 终端设置，退出时恢复


def _flush_pending_tty_input() -> None:
    """丢弃模型生成输出期间用户输入但尚未读取的按键。"""
    try:
        fd = sys.stdin.fileno()
        if not os.isatty(fd):
            return
    except Exception:
        return

    try:
        import termios

        termios.tcflush(fd, termios.TCIFLUSH)
        return
    except Exception:
        pass

    try:
        while True:
            ready, _, _ = select.select([fd], [], [], 0)
            if not ready:
                break
            if not os.read(fd, 4096):
                break
    except Exception:
        return


def _restore_terminal() -> None:
    """把终端恢复到原始状态（回显、行缓冲等）。"""
    if _SAVED_TERM_ATTRS is None:
        return
    try:
        import termios

        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, _SAVED_TERM_ATTRS)
    except Exception:
        pass


def _init_prompt_session() -> None:
    """创建带持久化文件历史的 prompt_toolkit 会话。"""
    global _PROMPT_SESSION, _SAVED_TERM_ATTRS

    # 保存终端状态，便于退出时恢复
    try:
        import termios

        _SAVED_TERM_ATTRS = termios.tcgetattr(sys.stdin.fileno())
    except Exception:
        pass

    from firefly.config.paths import get_cli_history_path

    history_file = get_cli_history_path()
    history_file.parent.mkdir(parents=True, exist_ok=True)

    _PROMPT_SESSION = PromptSession(
        history=SafeFileHistory(str(history_file)),
        enable_open_in_editor=False,
        multiline=False,  # Enter 提交（单行模式）
    )


def _make_console() -> Console:
    return Console(file=sys.stdout)


def _render_interactive_ansi(render_fn) -> str:
    """把 Rich 输出渲染为 ANSI，便于 prompt_toolkit 安全打印。"""
    ansi_console = Console(
        force_terminal=True,
        color_system=console.color_system or "standard",
        width=console.width,
    )
    with ansi_console.capture() as capture:
        render_fn(ansi_console)
    return capture.get()


def _print_agent_response(
    response: str,
    render_markdown: bool,
    metadata: dict | None = None,
) -> None:
    """以统一的终端样式渲染助手输出。"""
    console = _make_console()
    content = response or ""
    body = _response_renderable(content, render_markdown, metadata)
    console.print()
    console.print(f"[cyan]{__logo__}[/cyan]")
    console.print(body)
    console.print()


def _response_renderable(content: str, render_markdown: bool, metadata: dict | None = None):
    """渲染纯文本输出，避免 Markdown 折叠换行。"""
    if not render_markdown:
        return Text(content)
    if (metadata or {}).get("render_as") == "text":
        return Text(content)
    return Markdown(content)


async def _print_interactive_line(text: str) -> None:
    """用兼容 prompt_toolkit 的 Rich 样式输出异步交互更新。"""
    def _write() -> None:
        ansi = _render_interactive_ansi(
            lambda c: c.print(f"  [dim]↳ {text}[/dim]")
        )
        print_formatted_text(ANSI(ansi), end="")

    await run_in_terminal(_write)


async def _print_interactive_response(
    response: str,
    render_markdown: bool,
    metadata: dict | None = None,
) -> None:
    """用兼容 prompt_toolkit 的 Rich 样式输出异步交互回复。"""
    def _write() -> None:
        content = response or ""
        ansi = _render_interactive_ansi(
            lambda c: (
                c.print(),
                c.print(f"[cyan]{__logo__}[/cyan]"),
                c.print(_response_renderable(content, render_markdown, metadata)),
                c.print(),
            )
        )
        print_formatted_text(ANSI(ansi), end="")

    await run_in_terminal(_write)


def _print_cli_progress_line(text: str, thinking: ThinkingSpinner | None) -> None:
    """打印 CLI 进度行；必要时暂停 spinner。"""
    with thinking.pause() if thinking else nullcontext():
        console.print(f"  [dim]↳ {text}[/dim]")


async def _print_interactive_progress_line(text: str, thinking: ThinkingSpinner | None) -> None:
    """打印交互模式进度行；必要时暂停 spinner。"""
    with thinking.pause() if thinking else nullcontext():
        await _print_interactive_line(text)


def _is_exit_command(command: str) -> bool:
    """当输入应结束交互式聊天时返回 True。"""
    return command.lower() in EXIT_COMMANDS


def _has_interactive_console() -> bool:
    """检查当前进程是否具备可交互控制台。"""
    try:
        return bool(sys.stdin.isatty() and sys.stdout.isatty())
    except Exception:
        return False


async def _read_interactive_input_async() -> str:
    """使用 prompt_toolkit 读取用户输入（处理粘贴、历史、显示等）。

    prompt_toolkit 原生支持：
    - 多行粘贴（bracketed paste 模式）
    - 历史导航（上下方向键）
    - 干净显示（避免残影字符或显示伪影）
    """
    if _PROMPT_SESSION is None:
        raise RuntimeError("Call _init_prompt_session() first")
    try:
        with patch_stdout():
            return await _PROMPT_SESSION.prompt_async(
                HTML("<b fg='ansiblue'>You:</b> "),
            )
    except EOFError as exc:
        raise KeyboardInterrupt from exc


def version_callback(value: bool):
    if value:
        console.print(f"{__logo__} v{__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        None, "--version", "-v", callback=version_callback, is_eager=True
    ),
):
    """firefly - 个人 AI 助手。"""
    pass


# ============================================================================
# Onboard / 初始化
# ============================================================================


@app.command()
def onboard(
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    wizard: bool = typer.Option(False, "--wizard", help="Use interactive wizard"),
):
    """初始化 firefly 配置与 workspace。"""
    from firefly.config.loader import (
        get_config_path,
        load_config,
        save_config,
        set_config_path,
        write_project_context,
    )
    from firefly.config.schema import Config

    if config:
        config_path = Path(config).expanduser().resolve()
        set_config_path(config_path)
        console.print(f"[dim]Using config: {config_path}[/dim]")
    else:
        config_path = get_config_path()

    def _apply_workspace_override(loaded: Config) -> Config:
        if workspace:
            loaded.agents.defaults.workspace = workspace
        return loaded

    # 创建或更新配置
    if config_path.exists():
        if wizard:
            config = _apply_workspace_override(load_config(config_path))
        else:
            console.print(f"[yellow]Config already exists at {config_path}[/yellow]")
            console.print(
                "  [bold]y[/bold] = overwrite with defaults (existing values will be lost)"
            )
            console.print(
                "  [bold]N[/bold] = refresh config, keeping existing values and adding new fields"
            )
            if typer.confirm("Overwrite?"):
                config = _apply_workspace_override(Config())
                save_config(config, config_path)
                console.print(f"[green]✓[/green] Config reset to defaults at {config_path}")
            else:
                config = _apply_workspace_override(load_config(config_path))
                save_config(config, config_path)
                console.print(
                    f"[green]✓[/green] Config refreshed at {config_path} (existing values preserved)"
                )
    else:
        config = _apply_workspace_override(Config())
        # wizard 模式下先不保存：向导会在 should_save=True 时负责保存
        if not wizard:
            save_config(config, config_path)
            console.print(f"[green]✓[/green] Created config at {config_path}")

    # 如启用 wizard，则运行交互式向导
    if wizard:
        from firefly.cli.onboard import run_onboard

        try:
            result = run_onboard(initial_config=config)
            if not result.should_save:
                console.print("[yellow]Configuration discarded. No changes were saved.[/yellow]")
                return

            config = result.config
            save_config(config, config_path)
            console.print(f"[green]✓[/green] Config saved at {config_path}")
        except Exception as e:
            console.print(f"[red]✗[/red] Error during configuration: {e}")
            console.print("[yellow]Please run 'firefly onboard' again to complete setup.[/yellow]")
            raise typer.Exit(1)
    _onboard_plugins(config_path)

    # 创建 workspace（优先使用配置中的 workspace 路径）
    workspace_path = get_workspace_path(config.workspace_path)
    if not workspace_path.exists():
        workspace_path.mkdir(parents=True, exist_ok=True)
        console.print(f"[green]✓[/green] Created workspace at {workspace_path}")

    sync_workspace_templates(workspace_path)
    try:
        write_project_context(
            config_path=config_path,
            workspace=workspace_path,
        )
    except Exception:
        pass

    try:
        from firefly.config.cli_prefs import ensure_cli_prefs_file

        ensure_cli_prefs_file(config_path)
    except Exception:
        pass

    agent_cmd = 'firefly agent -m "Hello!"'
    gateway_cmd = "firefly gateway"
    if config:
        agent_cmd += f" --config {config_path}"
        gateway_cmd += f" --config {config_path}"

    console.print(f"\n{__logo__} is ready!")
    console.print("\nNext steps:")
    if wizard:
        console.print(f"  1. Chat: [cyan]{agent_cmd}[/cyan]")
        console.print(f"  2. Start gateway: [cyan]{gateway_cmd}[/cyan]")
    else:
        console.print(f"  1. Add your API key to [cyan]{config_path}[/cyan]")
        console.print("     Get one at: https://openrouter.ai/keys")
        console.print(f"  2. Chat: [cyan]{agent_cmd}[/cyan]")
    console.print(
        "\n[dim]Want Telegram/WhatsApp? See: https://github.com/HKUDS/firefly#-chat-apps[/dim]"
    )


def _merge_missing_defaults(existing: Any, defaults: Any) -> Any:
    """递归地用默认值补齐缺失字段，但不覆盖用户已有配置。"""
    if not isinstance(existing, dict) or not isinstance(defaults, dict):
        return existing

    merged = dict(existing)
    for key, value in defaults.items():
        if key not in merged:
            merged[key] = value
        else:
            merged[key] = _merge_missing_defaults(merged[key], value)
    return merged


def _onboard_plugins(config_path: Path) -> None:
    """为所有发现的 channel（内置 + 插件）注入默认配置。"""
    import json

    from firefly.channels.registry import discover_all

    all_channels = discover_all()
    if not all_channels:
        return

    with open(config_path, encoding="utf-8") as f:
        data = json.load(f)

    channels = data.setdefault("channels", {})
    for name, cls in all_channels.items():
        if name not in channels:
            channels[name] = cls.default_config()
        else:
            channels[name] = _merge_missing_defaults(channels[name], cls.default_config())

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _make_provider(config: Config):
    """根据配置创建合适的 LLM Provider。

    路由依据 registry 中的 ``ProviderSpec.backend`` 决定。
    """
    from firefly.providers.base import GenerationSettings
    from firefly.providers.registry import find_by_name

    model = config.agents.defaults.model
    provider_name = config.get_provider_name(model)
    p = config.get_provider(model)
    spec = find_by_name(provider_name) if provider_name else None
    backend = spec.backend if spec else "openai_compat"

    # --- 校验 ---
    if backend == "azure_openai":
        if not p or not p.api_key or not p.api_base:
            console.print("[red]Error: Azure OpenAI requires api_key and api_base.[/red]")
            console.print("Set them in ./config.json under providers.azure_openai section (or use --config).")
            console.print("Use the model field to specify the deployment name.")
            raise typer.Exit(1)
    elif backend == "openai_compat" and not model.startswith("bedrock/"):
        needs_key = not (p and p.api_key)
        exempt = spec and (spec.is_oauth or spec.is_local or spec.is_direct)
        if needs_key and not exempt:
            console.print("[red]Error: No API key configured.[/red]")
            console.print("Set one in ./config.json under providers section (or use --config).")
            raise typer.Exit(1)

    # --- 按 backend 实例化 ---
    if backend == "openai_codex":
        from firefly.providers.openai_codex_provider import OpenAICodexProvider

        provider = OpenAICodexProvider(default_model=model)
    elif backend == "azure_openai":
        from firefly.providers.azure_openai_provider import AzureOpenAIProvider

        provider = AzureOpenAIProvider(
            api_key=p.api_key,
            api_base=p.api_base,
            default_model=model,
        )
    elif backend == "github_copilot":
        from firefly.providers.github_copilot_provider import GitHubCopilotProvider
        provider = GitHubCopilotProvider(default_model=model)
    elif backend == "anthropic":
        from firefly.providers.anthropic_provider import AnthropicProvider

        provider = AnthropicProvider(
            api_key=p.api_key if p else None,
            api_base=config.get_api_base(model),
            default_model=model,
            extra_headers=p.extra_headers if p else None,
        )
    else:
        from firefly.providers.openai_compat_provider import OpenAICompatProvider

        provider = OpenAICompatProvider(
            api_key=p.api_key if p else None,
            api_base=config.get_api_base(model),
            default_model=model,
            extra_headers=p.extra_headers if p else None,
            spec=spec,
        )

    defaults = config.agents.defaults
    provider.generation = GenerationSettings(
        temperature=defaults.temperature,
        max_tokens=defaults.max_tokens,
        reasoning_effort=defaults.reasoning_effort,
    )
    return provider


def _load_runtime_config(config: str | None = None, workspace: str | None = None) -> Config:
    """加载配置，并可选地覆盖当前生效的 workspace。"""
    from firefly.config.loader import load_config, resolve_config_env_vars, set_config_path

    config_path = None
    if config:
        config_path = Path(config).expanduser().resolve()
        if not config_path.exists():
            console.print(f"[red]Error: Config file not found: {config_path}[/red]")
            raise typer.Exit(1)
        set_config_path(config_path)
        console.print(f"[dim]Using config: {config_path}[/dim]")

    try:
        loaded = resolve_config_env_vars(load_config(config_path))
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)
    _warn_deprecated_config_keys(config_path)
    if workspace:
        loaded.agents.defaults.workspace = workspace
    return loaded


def _warn_deprecated_config_keys(config_path: Path | None) -> None:
    """提示用户从配置文件里移除已废弃的键。"""
    import json

    from firefly.config.loader import get_config_path

    path = config_path or get_config_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return
    if "memoryWindow" in raw.get("agents", {}).get("defaults", {}):
        console.print(
            "[dim]提示：配置中的 `memoryWindow` 已不再使用，可以安全移除。[/dim]"
        )


def _migrate_cron_store(config: "Config") -> None:
    """一次性迁移：把旧的全局 cron 存储迁移到 workspace 内。"""
    from firefly.config.paths import get_cron_dir

    legacy_path = get_cron_dir() / "jobs.json"
    new_path = config.workspace_path / "cron" / "jobs.json"
    if legacy_path.is_file() and not new_path.exists():
        new_path.parent.mkdir(parents=True, exist_ok=True)
        import shutil

        shutil.move(str(legacy_path), str(new_path))


# ============================================================================
# OpenAI 兼容 API 服务
# ============================================================================


@app.command()
def serve(
    port: int | None = typer.Option(None, "--port", "-p", help="API server port"),
    host: str | None = typer.Option(None, "--host", "-H", help="Bind address"),
    timeout: float | None = typer.Option(None, "--timeout", "-t", help="Per-request timeout (seconds)"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show firefly runtime logs"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
):
    """启动 OpenAI 兼容的 API 服务（/v1/chat/completions）。"""
    try:
        from aiohttp import web  # noqa: F401
    except ImportError:
        console.print("[red]aiohttp is required. Install with: pip install 'firefly-ai[api]'[/red]")
        raise typer.Exit(1)

    from loguru import logger
    from firefly.agent.loop import AgentLoop
    from firefly.api.server import create_app
    from firefly.bus.queue import MessageBus
    from firefly.session.manager import SessionManager

    if verbose:
        logger.enable("firefly")
    else:
        logger.disable("firefly")

    runtime_config = _load_runtime_config(config, workspace)
    api_cfg = runtime_config.api
    host = host if host is not None else api_cfg.host
    port = port if port is not None else api_cfg.port
    timeout = timeout if timeout is not None else api_cfg.timeout
    sync_workspace_templates(runtime_config.workspace_path)
    bus = MessageBus()
    provider = _make_provider(runtime_config)
    session_manager = SessionManager(runtime_config.workspace_path)
    agent_loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=runtime_config.workspace_path,
        model=runtime_config.agents.defaults.model,
        max_iterations=runtime_config.agents.defaults.max_tool_iterations,
        context_window_tokens=runtime_config.agents.defaults.context_window_tokens,
        context_block_limit=runtime_config.agents.defaults.context_block_limit,
        max_tool_result_chars=runtime_config.agents.defaults.max_tool_result_chars,
        provider_retry_mode=runtime_config.agents.defaults.provider_retry_mode,
        web_config=runtime_config.tools.web,
        exec_config=runtime_config.tools.exec,
        restrict_to_workspace=runtime_config.tools.restrict_to_workspace,
        session_manager=session_manager,
        mcp_servers=runtime_config.tools.mcp_servers,
        channels_config=runtime_config.channels,
        timezone=runtime_config.agents.defaults.timezone,
        unified_session=runtime_config.agents.defaults.unified_session,
        disabled_skills=runtime_config.agents.defaults.disabled_skills,
        session_ttl_minutes=runtime_config.agents.defaults.session_ttl_minutes,
    )

    model_name = runtime_config.agents.defaults.model
    console.print(f"{__logo__} Starting OpenAI-compatible API server")
    console.print(f"  [cyan]Endpoint[/cyan] : http://{host}:{port}/v1/chat/completions")
    console.print(f"  [cyan]Model[/cyan]    : {model_name}")
    console.print("  [cyan]Session[/cyan]  : api:default")
    console.print(f"  [cyan]Timeout[/cyan]  : {timeout}s")
    if host in {"0.0.0.0", "::"}:
        console.print(
            "[yellow]Warning:[/yellow] API is bound to all interfaces. "
            "Only do this behind a trusted network boundary, firewall, or reverse proxy."
        )
    console.print()

    api_app = create_app(agent_loop, model_name=model_name, request_timeout=timeout)

    async def on_startup(_app):
        await agent_loop._connect_mcp()

    async def on_cleanup(_app):
        await agent_loop.close_mcp()

    api_app.on_startup.append(on_startup)
    api_app.on_cleanup.append(on_cleanup)

    web.run_app(api_app, host=host, port=port, print=lambda msg: logger.info(msg))


# ============================================================================
# Gateway / 服务端
# ============================================================================


@app.command()
def gateway(
    port: int | None = typer.Option(None, "--port", "-p", help="Gateway port"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
):
    """启动 firefly gateway。"""
    from firefly.agent.loop import AgentLoop
    from firefly.bus.queue import MessageBus
    from firefly.channels.manager import ChannelManager
    from firefly.cron.service import CronService
    from firefly.cron.types import CronJob
    from firefly.heartbeat.service import HeartbeatService
    from firefly.session.manager import SessionManager

    if verbose:
        import logging

        logging.basicConfig(level=logging.DEBUG)

    config = _load_runtime_config(config, workspace)
    port = port if port is not None else config.gateway.port

    console.print(f"{__logo__} Starting gateway version {__version__} on port {port}...")
    sync_workspace_templates(config.workspace_path)
    bus = MessageBus()
    provider = _make_provider(config)
    session_manager = SessionManager(config.workspace_path)

    # 兼容既有的单-workspace 安装，但保持自定义 workspace 的结构干净
    if is_default_workspace(config.workspace_path):
        _migrate_cron_store(config)

    # 创建 cron 服务（存储限定在 workspace 范围内）
    cron_store_path = config.workspace_path / "cron" / "jobs.json"
    cron = CronService(cron_store_path)

    # 创建带 cron 的 agent
    agent = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=config.workspace_path,
        model=config.agents.defaults.model,
        max_iterations=config.agents.defaults.max_tool_iterations,
        context_window_tokens=config.agents.defaults.context_window_tokens,
        web_config=config.tools.web,
        context_block_limit=config.agents.defaults.context_block_limit,
        max_tool_result_chars=config.agents.defaults.max_tool_result_chars,
        provider_retry_mode=config.agents.defaults.provider_retry_mode,
        exec_config=config.tools.exec,
        cron_service=cron,
        restrict_to_workspace=config.tools.restrict_to_workspace,
        session_manager=session_manager,
        mcp_servers=config.tools.mcp_servers,
        channels_config=config.channels,
        timezone=config.agents.defaults.timezone,
        unified_session=config.agents.defaults.unified_session,
        disabled_skills=config.agents.defaults.disabled_skills,
        session_ttl_minutes=config.agents.defaults.session_ttl_minutes,
    )

    # 设置 cron 回调（依赖 agent）
    async def on_cron_job(job: CronJob) -> str | None:
        """通过 agent 执行一个 cron 任务。"""
        # Dream 属于内部任务：直接运行，不走 agent loop
        if job.name == "dream":
            try:
                await agent.dream.run()
                logger.info("Dream cron job completed")
            except Exception:
                logger.exception("Dream cron job failed")
            return None

        from firefly.agent.tools.cron import CronTool
        from firefly.agent.tools.message import MessageTool
        from firefly.utils.evaluator import evaluate_response

        reminder_note = (
            "[Scheduled Task] Timer finished.\n\n"
            f"Task '{job.name}' has been triggered.\n"
            f"Scheduled instruction: {job.payload.message}"
        )

        cron_tool = agent.tools.get("cron")
        cron_token = None
        if isinstance(cron_tool, CronTool):
            cron_token = cron_tool.set_cron_context(True)
        try:
            resp = await agent.process_direct(
                reminder_note,
                session_key=f"cron:{job.id}",
                channel=job.payload.channel or "cli",
                chat_id=job.payload.to or "direct",
            )
        finally:
            if isinstance(cron_tool, CronTool) and cron_token is not None:
                cron_tool.reset_cron_context(cron_token)

        response = resp.content if resp else ""

        message_tool = agent.tools.get("message")
        if isinstance(message_tool, MessageTool) and message_tool._sent_in_turn:
            return response

        if job.payload.deliver and job.payload.to and response:
            should_notify = await evaluate_response(
                response, reminder_note, provider, agent.model,
            )
            if should_notify:
                from firefly.bus.events import OutboundMessage
                await bus.publish_outbound(OutboundMessage(
                    channel=job.payload.channel or "cli",
                    chat_id=job.payload.to,
                    content=response,
                ))
        return response

    cron.on_job = on_cron_job

    # 创建 channel 管理器
    channels = ChannelManager(config, bus)

    def _pick_heartbeat_target() -> tuple[str, str]:
        """为心跳触发的消息选择一个可路由的 channel/chat 目标。"""
        enabled = set(channels.enabled_channels)
        # 优先选择启用的、最近更新的非内部会话
        for item in session_manager.list_sessions():
            key = item.get("key") or ""
            if ":" not in key:
                continue
            channel, chat_id = key.split(":", 1)
            if channel in {"cli", "system"}:
                continue
            if channel in enabled and chat_id:
                return channel, chat_id
        # 回退：保持旧行为，但显式返回
        return "cli", "direct"

    # 创建心跳服务
    async def on_heartbeat_execute(tasks: str) -> str:
        """第二阶段：通过完整的 agent loop 执行心跳任务。"""
        channel, chat_id = _pick_heartbeat_target()

        async def _silent(*_args, **_kwargs):
            pass

        resp = await agent.process_direct(
            tasks,
            session_key="heartbeat",
            channel=channel,
            chat_id=chat_id,
            on_progress=_silent,
        )

        # 保留一小段心跳历史，既限制 loop 的规模
        # 又不至于在两次运行之间完全丢失短期上下文
        session = agent.sessions.get_or_create("heartbeat")
        session.retain_recent_legal_suffix(hb_cfg.keep_recent_messages)
        agent.sessions.save(session)

        return resp.content if resp else ""

    async def on_heartbeat_notify(response: str) -> None:
        """把心跳响应投递到用户的 channel。"""
        from firefly.bus.events import OutboundMessage
        channel, chat_id = _pick_heartbeat_target()
        if channel == "cli":
            return  # 没有可投递的外部 channel
        await bus.publish_outbound(OutboundMessage(channel=channel, chat_id=chat_id, content=response))

    hb_cfg = config.gateway.heartbeat
    heartbeat = HeartbeatService(
        workspace=config.workspace_path,
        provider=provider,
        model=agent.model,
        on_execute=on_heartbeat_execute,
        on_notify=on_heartbeat_notify,
        interval_s=hb_cfg.interval_s,
        enabled=hb_cfg.enabled,
        timezone=config.agents.defaults.timezone,
    )

    if channels.enabled_channels:
        console.print(f"[green]✓[/green] Channels enabled: {', '.join(channels.enabled_channels)}")
    else:
        console.print("[yellow]Warning: No channels enabled[/yellow]")

    cron_status = cron.status()
    if cron_status["jobs"] > 0:
        console.print(f"[green]✓[/green] Cron: {cron_status['jobs']} scheduled jobs")

    console.print(f"[green]✓[/green] Heartbeat: every {hb_cfg.interval_s}s")

    # 注册 Dream 系统任务（常驻；重启后重复注册也幂等）
    dream_cfg = config.agents.defaults.dream
    if dream_cfg.model_override:
        agent.dream.model = dream_cfg.model_override
    agent.dream.max_batch_size = dream_cfg.max_batch_size
    agent.dream.max_iterations = dream_cfg.max_iterations
    from firefly.cron.types import CronJob, CronPayload
    cron.register_system_job(CronJob(
        id="dream",
        name="dream",
        schedule=dream_cfg.build_schedule(config.agents.defaults.timezone),
        payload=CronPayload(kind="system_event"),
    ))
    console.print(f"[green]✓[/green] Dream: {dream_cfg.describe_schedule()}")

    async def run():
        try:
            await cron.start()
            await heartbeat.start()
            await asyncio.gather(
                agent.run(),
                channels.start_all(),
            )
        except KeyboardInterrupt:
            console.print("\nShutting down...")
        except Exception:
            import traceback

            console.print("\n[red]Error: Gateway crashed unexpectedly[/red]")
            console.print(traceback.format_exc())
        finally:
            await agent.close_mcp()
            heartbeat.stop()
            cron.stop()
            agent.stop()
            await channels.stop_all()

    asyncio.run(run())


# ============================================================================
# Agent 命令
# ============================================================================


@app.command()
def agent(
    message: str = typer.Option(None, "--message", "-m", help="Message to send to the agent"),
    session_id: str = typer.Option("cli:direct", "--session", "-s", help="Session ID"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Config file path"),
    markdown: bool = typer.Option(True, "--markdown/--no-markdown", help="Render assistant output as Markdown"),
    logs: bool = typer.Option(False, "--logs/--no-logs", help="Show firefly runtime logs during chat"),
    zotero_bridge_host: str = typer.Option("127.0.0.1", "--zotero-bridge-host", help="Zotero bridge bind host"),
    zotero_bridge_port: int = typer.Option(8765, "--zotero-bridge-port", help="Zotero bridge bind port"),
):
    """直接与 agent 交互。"""
    from loguru import logger

    from firefly.agent.loop import AgentLoop
    from firefly.bus.queue import MessageBus
    from firefly.cron.service import CronService

    config = _load_runtime_config(config, workspace)
    sync_workspace_templates(config.workspace_path)

    bus = MessageBus()
    provider = _make_provider(config)

    # 兼容既有的单-workspace 安装，但保持自定义 workspace 的结构干净
    if is_default_workspace(config.workspace_path):
        _migrate_cron_store(config)

    # 创建 cron 服务（存储限定在 workspace 范围内）
    cron_store_path = config.workspace_path / "cron" / "jobs.json"
    cron = CronService(cron_store_path)

    if logs:
        logger.enable("firefly")
    else:
        logger.disable("firefly")

    # 在 assistant 输出前展示本轮实际发给 LLM 的 messages/tools/参数（含多轮工具循环的每次请求）
    _llm_input_use_interactive: list[bool] = [True]

    async def _on_llm_request_display(payload: dict[str, Any]) -> None:
        """展示发给 LLM 的快照；任意异常不得阻断推理。"""
        title = "LLM 请求快照（默认整段脱敏；可在 cli.json 的 llm_input_print 中分段打印）"
        try:
            body = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        except Exception as exc:
            logger.warning("LLM request display: JSON encode failed ({}), using repr fallback", exc)
            body = repr(payload)[:120_000]

        def _print_plain() -> None:
            console.print(f"\n[bold yellow]{title}[/bold yellow]")
            console.print(body, style="dim", markup=False)
            console.print()

        if not _llm_input_use_interactive[0]:
            _print_plain()
            return
        try:
            def _write() -> None:
                ansi = _render_interactive_ansi(
                    lambda c: (
                        c.print(),
                        c.print(f"  [bold yellow]{title}[/bold yellow]"),
                        c.print(body, style="dim", markup=False),
                        c.print(),
                    )
                )
                print_formatted_text(ANSI(ansi), end="")
            await run_in_terminal(_write)
        except Exception as exc:
            logger.warning("LLM request display: run_in_terminal failed ({}), using direct print", exc)
            _print_plain()

    agent_loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=config.workspace_path,
        model=config.agents.defaults.model,
        max_iterations=config.agents.defaults.max_tool_iterations,
        context_window_tokens=config.agents.defaults.context_window_tokens,
        web_config=config.tools.web,
        context_block_limit=config.agents.defaults.context_block_limit,
        max_tool_result_chars=config.agents.defaults.max_tool_result_chars,
        provider_retry_mode=config.agents.defaults.provider_retry_mode,
        exec_config=config.tools.exec,
        cron_service=cron,
        restrict_to_workspace=config.tools.restrict_to_workspace,
        mcp_servers=config.tools.mcp_servers,
        channels_config=config.channels,
        timezone=config.agents.defaults.timezone,
        unified_session=config.agents.defaults.unified_session,
        disabled_skills=config.agents.defaults.disabled_skills,
        session_ttl_minutes=config.agents.defaults.session_ttl_minutes,
        on_llm_request=_on_llm_request_display,
    )
    restart_notice = consume_restart_notice_from_env()
    if restart_notice and should_show_cli_restart_notice(restart_notice, session_id):
        _print_agent_response(
            format_restart_completed_message(restart_notice.started_at_raw),
            render_markdown=False,
        )

    # progress 回调共享引用
    _thinking: ThinkingSpinner | None = None

    async def _cli_progress(content: str, *, tool_hint: bool = False) -> None:
        ch = agent_loop.channels_config
        if ch and tool_hint and not ch.send_tool_hints:
            return
        if ch and not tool_hint and not ch.send_progress:
            return
        _print_cli_progress_line(content, _thinking)

    if message:
        # 单条消息模式：直接调用，不需要 bus
        async def run_once():
            renderer = StreamRenderer(render_markdown=markdown)
            response = await agent_loop.process_direct(
                message, session_id,
                on_progress=_cli_progress,
                on_stream=renderer.on_delta,
                on_stream_end=renderer.on_end,
            )
            if not renderer.streamed:
                await renderer.close()
                _print_agent_response(
                    response.content if response else "",
                    render_markdown=markdown,
                    metadata=response.metadata if response else None,
                )
            await agent_loop.close_mcp()

        _llm_input_use_interactive[0] = _has_interactive_console()
        asyncio.run(run_once())
    else:
        # 交互模式：像其他 channel 一样通过 bus 路由
        from firefly.bus.events import InboundMessage
        interactive_console = _has_interactive_console()
        _llm_input_use_interactive[0] = interactive_console
        if interactive_console:
            _init_prompt_session()
            console.print(f"{__logo__} Interactive mode (type [bold]exit[/bold] or [bold]Ctrl+C[/bold] to quit)\n")
        else:
            console.print(
                f"{__logo__} Running in headless Zotero mode (no interactive console detected).\n"
            )

        if ":" in session_id:
            cli_channel, cli_chat_id = session_id.split(":", 1)
        else:
            cli_channel, cli_chat_id = "cli", session_id

        def _handle_signal(signum, frame):
            sig_name = signal.Signals(signum).name
            _restore_terminal()
            console.print(f"\nReceived {sig_name}, goodbye!")
            sys.exit(0)

        if interactive_console:
            signal.signal(signal.SIGINT, _handle_signal)
            signal.signal(signal.SIGTERM, _handle_signal)
            # Windows 不支持 SIGHUP
            if hasattr(signal, 'SIGHUP'):
                signal.signal(signal.SIGHUP, _handle_signal)
            # 忽略 SIGPIPE：避免写入已关闭的管道时进程静默退出
            # Windows 不支持 SIGPIPE
            if hasattr(signal, 'SIGPIPE'):
                signal.signal(signal.SIGPIPE, signal.SIG_IGN)

        async def run_interactive():
            from aiohttp import web

            bus_task = asyncio.create_task(agent_loop.run())
            turn_done = asyncio.Event()
            turn_done.set()
            turn_response: list[tuple[str, dict]] = []
            renderer: StreamRenderer | None = None
            inbound_from_zotero: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
            stream_subscribers: dict[str, set[asyncio.Queue[dict[str, str]]]] = {}
            zotero_chat_session_ids = tuple(f"zotero:chat-{idx}" for idx in range(1, 5))

            def _inject_zotero_item_context(content: str, source_session: str) -> str:
                """
                当 session_id 形如 "zotero:item-12345" 时，为用户消息注入当前条目上下文。

                这能让 agent 在 Zotero 面板会话里自动定位当前文献，而不要求用户手动给
                --item-id / --item-key 参数。
                """
                if not content:
                    return content

                if ":" in source_session:
                    channel, chat_id = source_session.split(":", 1)
                else:
                    channel, chat_id = "cli", source_session

                if channel != "zotero" or not chat_id.startswith("item-"):
                    return content

                raw_item_id = chat_id[len("item-") :].strip()
                if not raw_item_id.isdigit():
                    return content

                marker = f"[zotero_current_item_id={raw_item_id}]"
                if marker in content:
                    return content

                guidance = (
                    f"{marker}\n"
                    f"当前 Zotero 条目 item_id 为 {raw_item_id}。"
                    "若用户未显式指定其他文献，请优先基于该条目读取与分析。"
                )
                return f"{guidance}\n\n{content}"

            _WIKI_PDF_MARKER_RE = re.compile(r"\[zotero_current_wiki_pdf_path=(.+?)\]")
            _MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")

            def _extract_current_wiki_pdf_path(content: str) -> str:
                if not content:
                    return ""
                m = _WIKI_PDF_MARKER_RE.search(content)
                return str(m.group(1)).strip() if m else ""

            def _resolve_rag_path_from_pdf_marker(pdf_path: str) -> Path | None:
                if not pdf_path:
                    return None
                text = str(pdf_path)
                lowered = text.lower().replace("\\", "/")
                marker = "raw/pdf/"
                idx = lowered.find(marker)
                if idx < 0:
                    return None
                head = text[:idx]
                tail = text[idx + len(marker) :]
                return (Path(f"{head}raw/rag") / tail).with_suffix(".jsonl")

            def _resolve_markdown_root_from_rag_path(rag_path: Path) -> Path | None:
                text = str(rag_path)
                lowered = text.lower().replace("\\", "/")
                marker = "raw/rag/"
                idx = lowered.find(marker)
                if idx < 0:
                    return None
                head = text[:idx]
                return Path(f"{head}raw/markdown")

            def _rewrite_chunk_image_refs_for_multimodal(
                chunk_text: str, rec: dict[str, Any], rag_path: Path
            ) -> str:
                """把 chunk 中 markdown 图片引用改为绝对本地路径，供上游按原位注入图片。"""
                if not chunk_text or "![" not in chunk_text:
                    return chunk_text
                source_markdown = str(rec.get("source_markdown", "") or "").strip()
                if not source_markdown:
                    return chunk_text
                markdown_root = _resolve_markdown_root_from_rag_path(rag_path)
                if not markdown_root:
                    return chunk_text
                md_path = (markdown_root / source_markdown).resolve()
                md_parent = md_path.parent

                def _replace(match: re.Match[str]) -> str:
                    raw_ref = str(match.group(1) or "").strip().strip("<>")
                    # 兼容 markdown title：![alt](path "title")
                    if " " in raw_ref and not Path(raw_ref).exists():
                        raw_ref = raw_ref.split(" ", 1)[0].strip()
                    if not raw_ref:
                        return match.group(0)
                    ref_path = Path(raw_ref).expanduser()
                    candidate = ref_path if ref_path.is_absolute() else (md_parent / ref_path).resolve()
                    if not candidate.is_file():
                        return match.group(0)
                    return match.group(0).replace(match.group(1), str(candidate))

                return _MARKDOWN_IMAGE_RE.sub(_replace, chunk_text)

            def _tokenize_query(text: str) -> list[str]:
                # 支持中英文混合：中文按连续片段，英文数字按单词。
                return [
                    tok.lower()
                    for tok in re.findall(r"[\u4e00-\u9fff]{1,}|[A-Za-z0-9_]{2,}", text or "")
                    if tok
                ]

            def _should_inject_rag(content: str) -> bool:
                """仅在文献相关问题里注入 RAG，避免闲聊/泛问题触发检索。"""
                if not content:
                    return False
                text = str(content).strip()
                if not text:
                    return False
                lowered = text.lower()
                # 移除 marker 后再做语义判断，避免 marker 本身干扰。
                lowered = re.sub(r"\[zotero_current_[^\]]+\]", " ", lowered)
                lowered = re.sub(r"\s+", " ", lowered).strip()
                if not lowered:
                    return False

                # 明确无关的常见闲聊短句，直接不触发。
                off_topic_phrases = (
                    "你好",
                    "hi",
                    "hello",
                    "早上好",
                    "晚上好",
                    "在吗",
                    "谢谢",
                    "多谢",
                    "天气",
                    "吃什么",
                    "讲个笑话",
                    "今天几号",
                    "几点了",
                )
                if len(lowered) <= 32 and any(p in lowered for p in off_topic_phrases):
                    return False

                literature_keywords = (
                    "论文",
                    "文献",
                    "article",
                    "paper",
                    "preprint",
                    "arxiv",
                    "doi",
                    "pdf",
                    "摘要",
                    "abstract",
                    "引言",
                    "introduction",
                    "方法",
                    "method",
                    "实验",
                    "result",
                    "结果",
                    "结论",
                    "conclusion",
                    "贡献",
                    "局限",
                    "参考文献",
                    "citation",
                    "附录",
                    "图 ",
                    "表 ",
                )
                if any(k in lowered for k in literature_keywords):
                    return True

                # 指代当前打开文献/段落的问题，也视为文献相关。
                reference_patterns = (
                    r"(这篇|该文|本文|文中|这份|这个)\s*(论文|文献|pdf|文章)?",
                    r"(这一段|这段|这一节|本节|上文|下文|上面这段|下面这段)",
                    r"(作者|研究者)\s*(认为|提出|怎么做|如何做)",
                )
                return any(re.search(pat, lowered) for pat in reference_patterns)

            def _retrieve_local_rag_chunks(query: str, rag_jsonl_path: Path, top_k: int = 6) -> list[dict[str, Any]]:
                if not rag_jsonl_path.is_file():
                    return []
                tokens = _tokenize_query(query)
                if not tokens:
                    return []
                try:
                    lines = rag_jsonl_path.read_text(encoding="utf-8", errors="ignore").splitlines()
                except Exception:
                    return []
                scored: list[tuple[int, dict[str, Any]]] = []
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    text = str(rec.get("text", "") or "")
                    score = 0
                    lowered = text.lower()
                    for tok in tokens:
                        if tok in lowered:
                            score += 1
                    if score <= 0:
                        continue
                    scored.append((score, rec))
                scored.sort(key=lambda x: x[0], reverse=True)
                return [rec for _, rec in scored[: max(1, top_k)]]

            def _inject_rag_context(content: str) -> str:
                if not content:
                    return content
                if "[RAG Context]" in content:
                    return content
                if not _should_inject_rag(content):
                    return content
                pdf_path = _extract_current_wiki_pdf_path(content)
                if not pdf_path:
                    return content
                rag_path = _resolve_rag_path_from_pdf_marker(pdf_path)
                if not rag_path:
                    return content
                chunks = _retrieve_local_rag_chunks(content, rag_path)
                if not chunks:
                    return content
                block_lines = [
                    "[RAG Context]",
                    f"source: {rag_path}",
                    "以下片段来自当前打开文献的本地 RAG 检索（按相关度排序）：",
                    "重要：RAG 识别出的公式、符号、上下标可能存在 OCR/解析误差。请先校正并优化公式表达，再给出答案，不要逐字照搬原片段。",
                    "公式输出格式要求：请优先使用标准 LaTeX；独立公式单独成行并使用 $$...$$，行内公式使用 $...$；变量下标/上标请使用规范写法（如 f_{g,l}, p_{opt}）。",
                ]
                for i, rec in enumerate(chunks, start=1):
                    txt = str(rec.get("text", "") or "").strip()
                    txt = _rewrite_chunk_image_refs_for_multimodal(txt, rec, rag_path)
                    if len(txt) > 900:
                        txt = txt[:900] + " ..."
                    block_lines.append(f"--- chunk {i} (index={rec.get('chunk_index', i - 1)}) ---")
                    block_lines.append(txt)
                block_lines.append("[/RAG Context]")
                return f"{chr(10).join(block_lines)}\n\n{content}"

            def _cache_zotero_media(media_paths: list[str]) -> list[str]:
                """把前端传来的图片缓存到 workspace/temp，返回可读路径列表。"""
                if not media_paths:
                    return []
                temp_dir = config.workspace_path / "temp"
                temp_dir.mkdir(parents=True, exist_ok=True)
                cached: list[str] = []
                for raw in media_paths:
                    p = Path(str(raw)).expanduser()
                    if not p.is_file():
                        continue
                    suffix = p.suffix or ".png"
                    name = f"zotero-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}{suffix}"
                    target = temp_dir / name
                    try:
                        target.write_bytes(p.read_bytes())
                        cached.append(str(target))
                    except Exception as exc:
                        logger.warning("Cache zotero media failed: {} ({})", str(p), exc)
                return cached

            def _stream_subscribe(chat_id: str) -> asyncio.Queue[dict[str, str]]:
                queue: asyncio.Queue[dict[str, str]] = asyncio.Queue()
                stream_subscribers.setdefault(chat_id, set()).add(queue)
                return queue

            def _stream_unsubscribe(chat_id: str, queue: asyncio.Queue[dict[str, str]]) -> None:
                members = stream_subscribers.get(chat_id)
                if not members:
                    return
                members.discard(queue)
                if not members:
                    stream_subscribers.pop(chat_id, None)

            async def _stream_publish(chat_id: str, payload: dict[str, str]) -> None:
                members = stream_subscribers.get(chat_id)
                if not members:
                    return
                for q in list(members):
                    await q.put(payload)

            async def _start_zotero_bridge():
                def _serialize_session_messages(session_key: str) -> list[dict[str, str]]:
                    session = agent_loop.sessions.get_or_create(session_key)
                    out: list[dict[str, str]] = []
                    for msg in session.messages:
                        role = str(msg.get("role", ""))
                        if role not in {"user", "assistant"}:
                            continue
                        content = str(msg.get("content", "") or "")
                        reasoning = str(msg.get("reasoning_content", "") or "")
                        out.append({
                            "role": role,
                            "content": content,
                            "reasoning_content": reasoning,
                        })
                    return out

                async def _health(_request: web.Request) -> web.Response:
                    return web.json_response({"status": "ok"})

                async def _meta(_request: web.Request) -> web.Response:
                    return web.json_response(
                        {
                            "ok": True,
                            "model": str(config.agents.defaults.model or "").strip(),
                            "provider": str(config.agents.defaults.provider or "").strip(),
                        }
                    )

                async def _history(request: web.Request) -> web.Response:
                    session_arg = str(request.query.get("session_id", "")).strip()
                    if session_arg:
                        target_sessions = [session_arg]
                    else:
                        target_sessions = list(zotero_chat_session_ids)
                    legacy_listing = agent_loop.sessions.list_sessions()
                    resolved_sessions: dict[str, str] = {}
                    for sid in target_sessions:
                        resolved = sid
                        if sid.startswith("zotero:chat-"):
                            suffix = sid.split("zotero:chat-", 1)[-1]
                            current_messages = _serialize_session_messages(sid)
                            if not current_messages and suffix.isdigit():
                                legacy_candidates = [
                                    row
                                    for row in legacy_listing
                                    if str(row.get("key", "")).startswith("zotero:item-")
                                    and str(row.get("key", "")).endswith(f"-chat-{suffix}")
                                ]
                                if legacy_candidates:
                                    legacy_candidates.sort(
                                        key=lambda row: str(row.get("updated_at", "")),
                                        reverse=True,
                                    )
                                    resolved = str(legacy_candidates[0].get("key") or sid)
                        resolved_sessions[sid] = resolved
                    payload = {
                        "ok": True,
                        "sessions": {
                            sid: _serialize_session_messages(resolved_sessions.get(sid, sid))
                            for sid in target_sessions
                        },
                    }
                    return web.json_response(payload)

                def _resolve_pdf_path(body: dict[str, Any]) -> Path | None:
                    wiki_pdf_path = str((body or {}).get("wiki_pdf_path", "")).strip()
                    if wiki_pdf_path:
                        p = Path(wiki_pdf_path).expanduser()
                        if p.is_file():
                            return p
                    pdf_path = str((body or {}).get("pdf_path", "")).strip()
                    if pdf_path:
                        p = Path(pdf_path).expanduser()
                        if p.is_file():
                            return p
                    pdf_dir = str((body or {}).get("pdf_dir", "")).strip()
                    pdf_name = str((body or {}).get("pdf_name", "")).strip()
                    if pdf_dir and pdf_name:
                        p = (Path(pdf_dir).expanduser() / pdf_name).resolve()
                        if p.is_file():
                            return p
                    return None

                def _path_has_raw_pdf_segment(p: Path) -> bool:
                    """True if path contains .../raw/pdf/... (case-insensitive, any separator)."""
                    parts = [x.lower() for x in p.parts]
                    for i in range(len(parts) - 1):
                        if parts[i] == "raw" and parts[i + 1] == "pdf":
                            return True
                    return False

                def _resolve_markdown_path(pdf_path: Path) -> Path:
                    """Map .../raw/pdf/<rel>.pdf -> .../raw/markdown/<rel>.md (mirrors pdf_to_markdown layout)."""
                    p = pdf_path.expanduser()
                    try:
                        p = p.resolve()
                    except Exception:
                        pass
                    parts = list(p.parts)
                    low = [x.lower() for x in parts]
                    for i in range(len(low) - 1):
                        if low[i] == "raw" and low[i + 1] == "pdf":
                            root = Path(parts[0]).joinpath(*parts[1:i]) if i > 0 else Path(parts[0])
                            tail_parts = parts[i + 2 :]
                            if not tail_parts:
                                return root / "raw" / "markdown" / "document.md"
                            rel = Path(*tail_parts)
                            return (root / "raw" / "markdown" / rel).with_suffix(".md")
                    return p.with_suffix(".md")

                def _pick_source_pdf_for_conversion(body: dict[str, Any]) -> Path | None:
                    """Prefer wiki mirror on disk; else Zotero attachment path; else pdf_dir/pdf_name."""
                    for key in ("wiki_pdf_path", "pdf_path"):
                        raw = str((body or {}).get(key, "")).strip()
                        if not raw:
                            continue
                        cand = Path(raw).expanduser()
                        try:
                            cand = cand.resolve()
                        except Exception:
                            pass
                        if cand.is_file():
                            return cand
                    pdf_dir = str((body or {}).get("pdf_dir", "")).strip()
                    pdf_name = str((body or {}).get("pdf_name", "")).strip()
                    if pdf_dir and pdf_name:
                        cand = (Path(pdf_dir).expanduser() / pdf_name)
                        try:
                            cand = cand.resolve()
                        except Exception:
                            pass
                        if cand.is_file():
                            return cand
                    return None

                def _markdown_output_path(body: dict[str, Any], source_pdf: Path) -> Path:
                    """When plugin sends llm-wiki raw/pdf layout, always write under raw/markdown."""
                    pdf_dir = str((body or {}).get("pdf_dir", "")).strip()
                    pdf_name = str((body or {}).get("pdf_name", "")).strip()
                    if pdf_dir and pdf_name:
                        sync_intent = Path(pdf_dir).expanduser() / pdf_name
                        if _path_has_raw_pdf_segment(sync_intent):
                            return _resolve_markdown_path(sync_intent)
                    return _resolve_markdown_path(source_pdf)

                def _resolve_rag_jsonl_from_markdown(markdown_path: Path) -> Path:
                    """与 llm-wiki 目录约定一致：raw/markdown 下 .md 对应 raw/rag 下同相对路径 .jsonl。"""
                    text = str(markdown_path)
                    marker = "raw\\markdown\\"
                    marker_alt = "raw/markdown/"
                    lowered = text.lower()
                    idx = lowered.find(marker)
                    if idx < 0:
                        idx = lowered.find(marker_alt)
                    if idx >= 0:
                        head = text[:idx]
                        tail = text[idx + len(marker) :] if lowered.find(marker) >= 0 else text[idx + len(marker_alt) :]
                        rag_root = Path(f"{head}raw/rag")
                        return (rag_root / tail).with_suffix(".jsonl")
                    return markdown_path.with_suffix(".jsonl")

                async def _ensure_markdown_rag_index(markdown_path: Path) -> dict[str, Any]:
                    md = markdown_path.expanduser().resolve()
                    if not md.is_file():
                        raise FileNotFoundError(f"markdown not found for rag: {md}")
                    rag_path = _resolve_rag_jsonl_from_markdown(md)
                    script_path = (
                        Path(__file__).resolve().parents[1]
                        / "skills"
                        / "markdown"
                        / "scripts"
                        / "markdown_to_rag.py"
                    )
                    if not script_path.is_file():
                        raise FileNotFoundError(f"markdown_to_rag script not found: {script_path}")
                    proc = await asyncio.create_subprocess_exec(
                        sys.executable,
                        str(script_path),
                        "--markdown",
                        str(md),
                        "--rag",
                        str(rag_path.expanduser().resolve()),
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    stdout_b, stderr_b = await proc.communicate()
                    stdout = stdout_b.decode("utf-8", errors="replace")
                    stderr = stderr_b.decode("utf-8", errors="replace")
                    if proc.returncode != 0:
                        raise RuntimeError(
                            f"rag index failed (code={proc.returncode}): {stderr or stdout}".strip()
                        )
                    chunk_count = 0
                    try:
                        payload = json.loads(stdout.strip() or "{}")
                        inner = payload.get("result")
                        if isinstance(inner, dict):
                            chunk_count = int(inner.get("chunk_count") or 0)
                    except Exception:
                        chunk_count = 0
                    return {
                        "rag_path": str(rag_path),
                        "rag_chunk_count": chunk_count,
                    }

                async def _ensure_pdf_converted(pdf_path: Path, markdown_path: Path) -> dict[str, Any]:
                    if markdown_path.is_file():
                        return {
                            "converted": False,
                            "reason": "already_converted",
                        }

                    script_path = (
                        Path(__file__).resolve().parents[1]
                        / "skills"
                        / "markdown"
                        / "scripts"
                        / "pdf_to_markdown.py"
                    )
                    if not script_path.is_file():
                        raise FileNotFoundError(f"converter script not found: {script_path}")

                    proc = await asyncio.create_subprocess_exec(
                        sys.executable,
                        str(script_path),
                        "--pdf",
                        str(pdf_path),
                        "--markdown",
                        str(markdown_path),
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    stdout_b, stderr_b = await proc.communicate()
                    stdout = stdout_b.decode("utf-8", errors="replace")
                    stderr = stderr_b.decode("utf-8", errors="replace")
                    if proc.returncode != 0:
                        raise RuntimeError(
                            f"pdf convert failed (code={proc.returncode}): {stderr or stdout}".strip()
                        )
                    return {
                        "converted": True,
                        "stdout": stdout.strip(),
                    }

                async def _pdf_opened(request: web.Request) -> web.Response:
                    return web.json_response(
                        {
                            "ok": True,
                            "enabled": False,
                            "reason": "auto_pdf_conversion_disabled",
                        }
                    )

                async def _convert_markdown(request: web.Request) -> web.Response:
                    try:
                        body = await request.json()
                    except Exception:
                        return web.json_response({"ok": False, "error": "Invalid JSON body"}, status=400)

                    pdf_path = _pick_source_pdf_for_conversion(body or {})
                    if pdf_path is None:
                        return web.json_response(
                            {
                                "ok": False,
                                "error": (
                                    "PDF file not found. Sync or open the attachment so wiki_pdf_path / "
                                    "pdf_path exists, or ensure pdf_dir+pdf_name points to a real file."
                                ),
                            },
                            status=400,
                        )

                    markdown_path = _markdown_output_path(body or {}, pdf_path)
                    try:
                        result = await _ensure_pdf_converted(pdf_path, markdown_path)
                    except Exception as exc:
                        return web.json_response(
                            {
                                "ok": False,
                                "error": str(exc),
                                "pdf_path": str(pdf_path),
                                "markdown_path": str(markdown_path),
                            },
                            status=500,
                        )

                    try:
                        rag_meta = await _ensure_markdown_rag_index(markdown_path)
                    except Exception as exc:
                        return web.json_response(
                            {
                                "ok": False,
                                "error": str(exc),
                                "pdf_path": str(pdf_path),
                                "markdown_path": str(markdown_path),
                                "markdown_conversion": result,
                            },
                            status=500,
                        )

                    return web.json_response(
                        {
                            "ok": True,
                            "pdf_path": str(pdf_path),
                            "markdown_path": str(markdown_path),
                            **result,
                            **rag_meta,
                        }
                    )

                async def _ingest(request: web.Request) -> web.Response:
                    try:
                        body = await request.json()
                    except Exception:
                        return web.json_response({"ok": False, "error": "Invalid JSON body"}, status=400)

                    content = str((body or {}).get("message", "")).strip()
                    if not content:
                        return web.json_response({"ok": False, "error": "message is required"}, status=400)

                    override = str((body or {}).get("session_id", "")).strip()
                    media_paths = (body or {}).get("media")
                    if not isinstance(media_paths, list):
                        media_paths = []
                    media_paths = [str(p).strip() for p in media_paths if str(p).strip()]
                    cached_media = _cache_zotero_media(media_paths)
                    patched = _inject_zotero_item_context(content, override or session_id)
                    patched = _inject_rag_context(patched)
                    await inbound_from_zotero.put({
                        "message": patched,
                        "session_id": override,
                        "media": cached_media,
                    })
                    return web.json_response({"ok": True})

                async def _stream_chat(request: web.Request) -> web.StreamResponse:
                    try:
                        body = await request.json()
                    except Exception:
                        return web.json_response({"ok": False, "error": "Invalid JSON body"}, status=400)

                    content = str((body or {}).get("message", "")).strip()
                    if not content:
                        return web.json_response({"ok": False, "error": "message is required"}, status=400)
                    raw_thinking_state = str((body or {}).get("thinking_state", "Enable")).strip().lower()
                    thinking_state = "Disable" if raw_thinking_state == "disable" else "Enable"
                    media_paths = (body or {}).get("media")
                    if not isinstance(media_paths, list):
                        media_paths = []
                    media_paths = [str(p).strip() for p in media_paths if str(p).strip()]
                    cached_media = _cache_zotero_media(media_paths)

                    source_session = str((body or {}).get("session_id", "")).strip() or session_id
                    if ":" in source_session:
                        current_channel, current_chat_id = source_session.split(":", 1)
                    else:
                        current_channel, current_chat_id = "cli", source_session
                    patched_content = _inject_zotero_item_context(content, source_session)
                    patched_content = _inject_rag_context(patched_content)

                    subscriber = _stream_subscribe(current_chat_id)
                    response = web.StreamResponse(
                        status=200,
                        headers={
                            "Content-Type": "text/event-stream; charset=utf-8",
                            "Cache-Control": "no-cache",
                            "Connection": "keep-alive",
                        },
                    )
                    await response.prepare(request)

                    async def _write_event(event: dict[str, str]) -> None:
                        packet = f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8")
                        await response.write(packet)

                    from firefly.bus.events import InboundMessage
                    await bus.publish_inbound(InboundMessage(
                        channel=current_channel,
                        sender_id="user",
                        chat_id=current_chat_id,
                        content=patched_content,
                        media=cached_media,
                        metadata={
                            "_wants_stream": True,
                            "_source": "zotero_stream",
                            "_thinking_state": thinking_state,
                        },
                    ))

                    try:
                        await _write_event({"type": "start"})
                        while True:
                            event = await asyncio.wait_for(subscriber.get(), timeout=120.0)
                            await _write_event(event)
                            # "end" 仅表示一个流片段结束（可能随后还有最终 final）
                            # 仅在 final/error 时关闭 SSE，避免丢失 thinking/最终正文。
                            if event.get("type") in {"final", "error"}:
                                break
                    except asyncio.TimeoutError:
                        await _write_event({"type": "error", "message": "stream timeout"})
                    finally:
                        _stream_unsubscribe(current_chat_id, subscriber)
                        await response.write_eof()
                    return response

                async def _cancel_chat(request: web.Request) -> web.Response:
                    try:
                        body = await request.json()
                    except Exception:
                        body = {}

                    source_session = str((body or {}).get("session_id", "")).strip() or session_id
                    if ":" in source_session:
                        current_channel, current_chat_id = source_session.split(":", 1)
                    else:
                        current_channel, current_chat_id = "cli", source_session

                    from firefly.bus.events import InboundMessage
                    await bus.publish_inbound(InboundMessage(
                        channel=current_channel,
                        sender_id="user",
                        chat_id=current_chat_id,
                        content="/stop",
                        metadata={
                            "_source": "zotero_cancel",
                            "_requested_by": "frontend_cancel_button",
                        },
                    ))
                    return web.json_response({"ok": True, "detail": "stop requested"})

                def _resolve_zotero_chat_session_key(requested_sid: str) -> str:
                    """与 /zotero/history 一致：chat-N 可能映射到 item 下的 legacy session key。"""
                    resolved = requested_sid
                    if requested_sid.startswith("zotero:chat-"):
                        suffix = requested_sid.split("zotero:chat-", 1)[-1]
                        probe = _serialize_session_messages(requested_sid)
                        if not probe and suffix.isdigit():
                            legacy_listing = agent_loop.sessions.list_sessions()
                            legacy_candidates = [
                                row
                                for row in legacy_listing
                                if str(row.get("key", "")).startswith("zotero:item-")
                                and str(row.get("key", "")).endswith(f"-chat-{suffix}")
                            ]
                            if legacy_candidates:
                                legacy_candidates.sort(
                                    key=lambda row: str(row.get("updated_at", "")),
                                    reverse=True,
                                )
                                resolved = str(legacy_candidates[0].get("key") or requested_sid)
                    return resolved

                async def _clear_session(request: web.Request) -> web.Response:
                    try:
                        body = await request.json()
                    except Exception:
                        body = {}
                    raw_sid = str((body or {}).get("session_id", "")).strip()
                    if not raw_sid:
                        return web.json_response({"ok": False, "error": "session_id is required"}, status=400)
                    target_key = _resolve_zotero_chat_session_key(raw_sid)
                    sess = agent_loop.sessions.get_or_create(target_key)
                    sess.clear()
                    agent_loop.sessions.save(sess)
                    return web.json_response({"ok": True, "session_id": target_key})

                app = web.Application()
                app.router.add_get("/health", _health)
                app.router.add_get("/zotero/meta", _meta)
                app.router.add_get("/zotero/history", _history)
                app.router.add_post("/zotero/message", _ingest)
                app.router.add_post("/zotero/stream", _stream_chat)
                app.router.add_post("/zotero/cancel", _cancel_chat)
                app.router.add_post("/zotero/session/clear", _clear_session)
                app.router.add_post("/zotero/pdf-opened", _pdf_opened)
                app.router.add_post("/zotero/convert-markdown", _convert_markdown)
                runner = web.AppRunner(app)
                await runner.setup()
                site = web.TCPSite(runner, zotero_bridge_host, zotero_bridge_port)
                await site.start()
                return runner

            bridge_runner = await _start_zotero_bridge()
            console.print(
                f"[green]✓[/green] Zotero bridge listening on "
                f"http://{zotero_bridge_host}:{zotero_bridge_port}/zotero/message"
            )
            console.print(
                "[dim]POST JSON: {\"message\": \"...\", \"session_id\": \"optional\", "
                "\"thinking_state\": \"Enable\"|\"Disable\"}[/dim]",
            )

            async def _consume_outbound():
                while True:
                    try:
                        msg = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)

                        if msg.metadata.get("_stream_delta"):
                            await _stream_publish(msg.chat_id, {"type": "delta", "delta": msg.content})
                            if renderer:
                                await renderer.on_delta(msg.content)
                            continue
                        if msg.metadata.get("_stream_reasoning_delta"):
                            await _stream_publish(msg.chat_id, {"type": "thinking_delta", "delta": msg.content})
                            continue
                        if msg.metadata.get("_stream_end"):
                            await _stream_publish(msg.chat_id, {"type": "end"})
                            if renderer:
                                await renderer.on_end(
                                    resuming=msg.metadata.get("_resuming", False),
                                )
                            continue
                        if msg.metadata.get("_streamed"):
                            if msg.content:
                                await _stream_publish(msg.chat_id, {"type": "final", "content": msg.content})
                                # 流式已在 StreamRenderer 中打到终端；仍向 Zotero 推送 final，但勿重复打印
                                if not (renderer and renderer.streamed):
                                    if interactive_console:
                                        await _print_interactive_response(
                                            msg.content,
                                            render_markdown=markdown,
                                            metadata=msg.metadata,
                                        )
                                    else:
                                        _print_agent_response(
                                            msg.content,
                                            render_markdown=markdown,
                                            metadata=msg.metadata,
                                        )
                            turn_done.set()
                            continue

                        if msg.metadata.get("_progress"):
                            is_tool_hint = msg.metadata.get("_tool_hint", False)
                            ch = agent_loop.channels_config
                            if ch and is_tool_hint and not ch.send_tool_hints:
                                pass
                            elif ch and not is_tool_hint and not ch.send_progress:
                                pass
                            else:
                                await _print_interactive_progress_line(msg.content, _thinking)
                            continue

                        if not turn_done.is_set():
                            if msg.content:
                                turn_response.append((msg.content, dict(msg.metadata or {})))
                                await _stream_publish(msg.chat_id, {"type": "final", "content": msg.content})
                            turn_done.set()
                        elif msg.content:
                            await _stream_publish(msg.chat_id, {"type": "final", "content": msg.content})
                            await _print_interactive_response(
                                msg.content,
                                render_markdown=markdown,
                                metadata=msg.metadata,
                            )

                    except asyncio.TimeoutError:
                        continue
                    except asyncio.CancelledError:
                        break

            outbound_task = asyncio.create_task(_consume_outbound())

            try:
                while True:
                    try:
                        source = "zotero"
                        source_session = session_id
                        if interactive_console:
                            _flush_pending_tty_input()
                            # 等待用户输入前停止 spinner，避免与 prompt_toolkit 冲突
                            if renderer:
                                renderer.stop_for_input()

                            input_task = asyncio.create_task(_read_interactive_input_async())
                            zotero_task = asyncio.create_task(inbound_from_zotero.get())
                            done, pending = await asyncio.wait(
                                {input_task, zotero_task},
                                return_when=asyncio.FIRST_COMPLETED,
                            )
                            for task in pending:
                                task.cancel()
                            await asyncio.gather(*pending, return_exceptions=True)

                            source = "cli"
                            if zotero_task in done:
                                payload = zotero_task.result()
                                user_input = payload["message"]
                                source = "zotero"
                                source_session = payload.get("session_id") or session_id
                                source_media = payload.get("media") if isinstance(payload.get("media"), list) else []
                                await _print_interactive_line(f"[Zotero] {user_input}")
                            else:
                                user_input = input_task.result()
                                source_media = []
                        else:
                            payload = await inbound_from_zotero.get()
                            user_input = payload["message"]
                            source_session = payload.get("session_id") or session_id
                            source_media = payload.get("media") if isinstance(payload.get("media"), list) else []
                        command = user_input.strip()
                        if not command:
                            continue

                        if interactive_console and _is_exit_command(command):
                            _restore_terminal()
                            console.print("\nGoodbye!")
                            break

                        turn_done.clear()
                        turn_response.clear()
                        renderer = StreamRenderer(render_markdown=markdown)

                        if ":" in source_session:
                            current_channel, current_chat_id = source_session.split(":", 1)
                        else:
                            current_channel, current_chat_id = "cli", source_session
                        patched_command = _inject_zotero_item_context(command, source_session)
                        patched_command = _inject_rag_context(patched_command)

                        await bus.publish_inbound(InboundMessage(
                            channel=current_channel,
                            sender_id="user",
                            chat_id=current_chat_id,
                            content=patched_command,
                            media=[str(p).strip() for p in source_media if str(p).strip()],
                            metadata={"_wants_stream": True, "_source": source},
                        ))

                        await turn_done.wait()

                        if turn_response:
                            content, meta = turn_response[0]
                            if content and not meta.get("_streamed"):
                                if renderer:
                                    await renderer.close()
                                _print_agent_response(
                                    content, render_markdown=markdown, metadata=meta,
                                )
                        elif renderer and not renderer.streamed:
                            await renderer.close()
                    except KeyboardInterrupt:
                        _restore_terminal()
                        console.print("\nGoodbye!")
                        break
                    except EOFError:
                        _restore_terminal()
                        console.print("\nGoodbye!")
                        break
            finally:
                agent_loop.stop()
                outbound_task.cancel()
                await asyncio.gather(bus_task, outbound_task, return_exceptions=True)
                await agent_loop.close_mcp()
                if bridge_runner is not None:
                    await bridge_runner.cleanup()

        asyncio.run(run_interactive())


# ============================================================================
# Channel 命令
# ============================================================================


channels_app = typer.Typer(help="Manage channels")
app.add_typer(channels_app, name="channels")


@channels_app.command("status")
def channels_status(
    config_path: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
):
    """显示 channel 状态。"""
    from firefly.channels.registry import discover_all
    from firefly.config.loader import load_config, set_config_path

    resolved_config_path = Path(config_path).expanduser().resolve() if config_path else None
    if resolved_config_path is not None:
        set_config_path(resolved_config_path)

    config = load_config(resolved_config_path)

    table = Table(title="Channel Status")
    table.add_column("Channel", style="cyan")
    table.add_column("Enabled")

    for name, cls in sorted(discover_all().items()):
        section = getattr(config.channels, name, None)
        if section is None:
            enabled = False
        elif isinstance(section, dict):
            enabled = section.get("enabled", False)
        else:
            enabled = getattr(section, "enabled", False)
        table.add_row(
            cls.display_name,
            "[green]\u2713[/green]" if enabled else "[dim]\u2717[/dim]",
        )

    console.print(table)


def _get_bridge_dir() -> Path:
    """获取 bridge 目录；必要时进行初始化。"""
    import shutil
    import subprocess

    # 用户侧 bridge 安装位置
    from firefly.config.paths import get_bridge_install_dir

    user_bridge = get_bridge_install_dir()

    # 检查是否已经构建
    if (user_bridge / "dist" / "index.js").exists():
        return user_bridge

    # 检查是否存在 npm
    npm_path = shutil.which("npm")
    if not npm_path:
        console.print("[red]npm not found. Please install Node.js >= 18.[/red]")
        raise typer.Exit(1)

    # 查找 bridge 源码：优先检查已安装包数据，其次检查源码目录
    pkg_bridge = Path(__file__).parent.parent / "bridge"  # firefly/bridge（已安装）
    src_bridge = Path(__file__).parent.parent.parent / "bridge"  # 仓库根/bridge（开发态）

    source = None
    if (pkg_bridge / "package.json").exists():
        source = pkg_bridge
    elif (src_bridge / "package.json").exists():
        source = src_bridge

    if not source:
        console.print("[red]Bridge source not found.[/red]")
        console.print("Try reinstalling: pip install --force-reinstall firefly")
        raise typer.Exit(1)

    console.print(f"{__logo__} Setting up bridge...")

    # 复制到用户目录
    user_bridge.parent.mkdir(parents=True, exist_ok=True)
    if user_bridge.exists():
        shutil.rmtree(user_bridge)
    shutil.copytree(source, user_bridge, ignore=shutil.ignore_patterns("node_modules", "dist"))

    # 安装依赖并构建
    try:
        console.print("  Installing dependencies...")
        subprocess.run([npm_path, "install"], cwd=user_bridge, check=True, capture_output=True)

        console.print("  Building...")
        subprocess.run([npm_path, "run", "build"], cwd=user_bridge, check=True, capture_output=True)

        console.print("[green]✓[/green] Bridge ready\n")
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Build failed: {e}[/red]")
        if e.stderr:
            console.print(f"[dim]{e.stderr.decode()[:500]}[/dim]")
        raise typer.Exit(1)

    return user_bridge


@channels_app.command("login")
def channels_login(
    channel_name: str = typer.Argument(..., help="Channel name (e.g. weixin, whatsapp)"),
    force: bool = typer.Option(False, "--force", "-f", help="Force re-authentication even if already logged in"),
    config_path: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
):
    """通过二维码或其他交互式方式登录某个 channel。"""
    from firefly.channels.registry import discover_all
    from firefly.config.loader import load_config, set_config_path

    resolved_config_path = Path(config_path).expanduser().resolve() if config_path else None
    if resolved_config_path is not None:
        set_config_path(resolved_config_path)

    config = load_config(resolved_config_path)
    channel_cfg = getattr(config.channels, channel_name, None) or {}

    # 校验 channel 是否存在
    all_channels = discover_all()
    if channel_name not in all_channels:
        available = ", ".join(all_channels.keys())
        console.print(f"[red]Unknown channel: {channel_name}[/red]  Available: {available}")
        raise typer.Exit(1)

    console.print(f"{__logo__} {all_channels[channel_name].display_name} Login\n")

    channel_cls = all_channels[channel_name]
    channel = channel_cls(channel_cfg, bus=None)

    success = asyncio.run(channel.login(force=force))

    if not success:
        raise typer.Exit(1)


# ============================================================================
# 插件命令
# ============================================================================

plugins_app = typer.Typer(help="Manage channel plugins")
app.add_typer(plugins_app, name="plugins")


@plugins_app.command("list")
def plugins_list():
    """列出所有发现的 channel（内置 + 插件）。"""
    from firefly.channels.registry import discover_all, discover_channel_names
    from firefly.config.loader import load_config

    config = load_config()
    builtin_names = set(discover_channel_names())
    all_channels = discover_all()

    table = Table(title="Channel Plugins")
    table.add_column("Name", style="cyan")
    table.add_column("Source", style="magenta")
    table.add_column("Enabled")

    for name in sorted(all_channels):
        cls = all_channels[name]
        source = "builtin" if name in builtin_names else "plugin"
        section = getattr(config.channels, name, None)
        if section is None:
            enabled = False
        elif isinstance(section, dict):
            enabled = section.get("enabled", False)
        else:
            enabled = getattr(section, "enabled", False)
        table.add_row(
            cls.display_name,
            source,
            "[green]yes[/green]" if enabled else "[dim]no[/dim]",
        )

    console.print(table)


# ============================================================================
# 状态命令
# ============================================================================


@app.command()
def status():
    """显示 firefly 状态。"""
    from firefly.config.loader import get_config_path, load_config

    config_path = get_config_path()
    config = load_config()
    workspace = config.workspace_path

    console.print(f"{__logo__} Status\n")

    console.print(f"Config: {config_path} {'[green]✓[/green]' if config_path.exists() else '[red]✗[/red]'}")
    console.print(f"Workspace: {workspace} {'[green]✓[/green]' if workspace.exists() else '[red]✗[/red]'}")

    if config_path.exists():
        from firefly.providers.registry import PROVIDERS

        console.print(f"Model: {config.agents.defaults.model}")

        # 从 registry 检查各 provider 的 API key 配置情况
        for spec in PROVIDERS:
            p = getattr(config.providers, spec.name, None)
            if p is None:
                continue
            if spec.is_oauth:
                console.print(f"{spec.label}: [green]✓ (OAuth)[/green]")
            elif spec.is_local:
                # 本地部署通常显示 api_base，而不是 api_key
                if p.api_base:
                    console.print(f"{spec.label}: [green]✓ {p.api_base}[/green]")
                else:
                    console.print(f"{spec.label}: [dim]not set[/dim]")
            else:
                has_key = bool(p.api_key)
                console.print(f"{spec.label}: {'[green]✓[/green]' if has_key else '[dim]not set[/dim]'}")


# ============================================================================
# OAuth 登录
# ============================================================================

provider_app = typer.Typer(help="Manage providers")
app.add_typer(provider_app, name="provider")


_LOGIN_HANDLERS: dict[str, callable] = {}


def _register_login(name: str):
    def decorator(fn):
        _LOGIN_HANDLERS[name] = fn
        return fn

    return decorator


@provider_app.command("login")
def provider_login(
    provider: str = typer.Argument(..., help="OAuth provider (e.g. 'openai-codex', 'github-copilot')"),
):
    """与某个 OAuth provider 完成认证。"""
    from firefly.providers.registry import PROVIDERS

    key = provider.replace("-", "_")
    spec = next((s for s in PROVIDERS if s.name == key and s.is_oauth), None)
    if not spec:
        names = ", ".join(s.name.replace("_", "-") for s in PROVIDERS if s.is_oauth)
        console.print(f"[red]Unknown OAuth provider: {provider}[/red]  Supported: {names}")
        raise typer.Exit(1)

    handler = _LOGIN_HANDLERS.get(spec.name)
    if not handler:
        console.print(f"[red]Login not implemented for {spec.label}[/red]")
        raise typer.Exit(1)

    console.print(f"{__logo__} OAuth Login - {spec.label}\n")
    handler()


@_register_login("openai_codex")
def _login_openai_codex() -> None:
    try:
        from oauth_cli_kit import get_token, login_oauth_interactive

        token = None
        try:
            token = get_token()
        except Exception:
            pass
        if not (token and token.access):
            console.print("[cyan]Starting interactive OAuth login...[/cyan]\n")
            token = login_oauth_interactive(
                print_fn=lambda s: console.print(s),
                prompt_fn=lambda s: typer.prompt(s),
            )
        if not (token and token.access):
            console.print("[red]✗ Authentication failed[/red]")
            raise typer.Exit(1)
        console.print(f"[green]✓ Authenticated with OpenAI Codex[/green]  [dim]{token.account_id}[/dim]")
    except ImportError:
        console.print("[red]oauth_cli_kit not installed. Run: pip install oauth-cli-kit[/red]")
        raise typer.Exit(1)


@_register_login("github_copilot")
def _login_github_copilot() -> None:
    try:
        from firefly.providers.github_copilot_provider import login_github_copilot

        console.print("[cyan]Starting GitHub Copilot device flow...[/cyan]\n")
        token = login_github_copilot(
            print_fn=lambda s: console.print(s),
            prompt_fn=lambda s: typer.prompt(s),
        )
        account = token.account_id or "GitHub"
        console.print(f"[green]✓ Authenticated with GitHub Copilot[/green]  [dim]{account}[/dim]")
    except Exception as e:
        console.print(f"[red]Authentication error: {e}[/red]")
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
