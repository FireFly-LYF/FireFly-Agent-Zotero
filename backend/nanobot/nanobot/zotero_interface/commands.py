"""nanobot 的zotero接口命令集合。"""

import asyncio
import json
import os
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

from nanobot import __logo__, __version__


class SafeFileHistory(FileHistory):
    """写入历史记录时清理代理字符（surrogate）的 FileHistory 子类。

    在 Windows 上，某些 Unicode 输入（例如 emoji、混合脚本）可能产生
    surrogate 字符，导致 prompt_toolkit 写文件时崩溃。
    参见 issue #2846。
    """

    def store_string(self, string: str) -> None:
        safe = string.encode("utf-8", errors="surrogateescape").decode("utf-8", errors="replace")
        super().store_string(safe)
from nanobot.cli.stream import StreamRenderer, ThinkingSpinner
from nanobot.config.paths import get_workspace_path, is_default_workspace
from nanobot.config.schema import Config
from nanobot.utils.helpers import sync_workspace_templates
from nanobot.utils.restart import (
    consume_restart_notice_from_env,
    format_restart_completed_message,
    should_show_cli_restart_notice,
)

app = typer.Typer(
    name="nanobot",
    context_settings={"help_option_names": ["-h", "--help"]},
    help=f"{__logo__} nanobot - Personal AI Assistant",
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

    from nanobot.config.paths import get_cli_history_path

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
    console.print(f"[cyan]{__logo__} nanobot[/cyan]")
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
                c.print(f"[cyan]{__logo__} nanobot[/cyan]"),
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
        console.print(f"{__logo__} nanobot v{__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        None, "--version", "-v", callback=version_callback, is_eager=True
    ),
):
    """nanobot - 个人 AI 助手。"""
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
    """初始化 nanobot 配置与 workspace。"""
    from nanobot.config.loader import get_config_path, load_config, save_config, set_config_path
    from nanobot.config.schema import Config

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
        from nanobot.cli.onboard import run_onboard

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
            console.print("[yellow]Please run 'nanobot onboard' again to complete setup.[/yellow]")
            raise typer.Exit(1)
    _onboard_plugins(config_path)

    # 创建 workspace（优先使用配置中的 workspace 路径）
    workspace_path = get_workspace_path(config.workspace_path)
    if not workspace_path.exists():
        workspace_path.mkdir(parents=True, exist_ok=True)
        console.print(f"[green]✓[/green] Created workspace at {workspace_path}")

    sync_workspace_templates(workspace_path)

    agent_cmd = 'nanobot agent -m "Hello!"'
    gateway_cmd = "nanobot gateway"
    if config:
        agent_cmd += f" --config {config_path}"
        gateway_cmd += f" --config {config_path}"

    console.print(f"\n{__logo__} nanobot is ready!")
    console.print("\nNext steps:")
    if wizard:
        console.print(f"  1. Chat: [cyan]{agent_cmd}[/cyan]")
        console.print(f"  2. Start gateway: [cyan]{gateway_cmd}[/cyan]")
    else:
        console.print(f"  1. Add your API key to [cyan]{config_path}[/cyan]")
        console.print("     Get one at: https://openrouter.ai/keys")
        console.print(f"  2. Chat: [cyan]{agent_cmd}[/cyan]")
    console.print(
        "\n[dim]Want Telegram/WhatsApp? See: https://github.com/HKUDS/nanobot#-chat-apps[/dim]"
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

    from nanobot.channels.registry import discover_all

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
    from nanobot.providers.base import GenerationSettings
    from nanobot.providers.registry import find_by_name

    model = config.agents.defaults.model
    provider_name = config.get_provider_name(model)
    p = config.get_provider(model)
    spec = find_by_name(provider_name) if provider_name else None
    backend = spec.backend if spec else "openai_compat"

    # --- 校验 ---
    if backend == "azure_openai":
        if not p or not p.api_key or not p.api_base:
            console.print("[red]Error: Azure OpenAI requires api_key and api_base.[/red]")
            console.print("Set them in ~/.nanobot/config.json under providers.azure_openai section")
            console.print("Use the model field to specify the deployment name.")
            raise typer.Exit(1)
    elif backend == "openai_compat" and not model.startswith("bedrock/"):
        needs_key = not (p and p.api_key)
        exempt = spec and (spec.is_oauth or spec.is_local or spec.is_direct)
        if needs_key and not exempt:
            console.print("[red]Error: No API key configured.[/red]")
            console.print("Set one in ~/.nanobot/config.json under providers section")
            raise typer.Exit(1)

    # --- 按 backend 实例化 ---
    if backend == "openai_codex":
        from nanobot.providers.openai_codex_provider import OpenAICodexProvider

        provider = OpenAICodexProvider(default_model=model)
    elif backend == "azure_openai":
        from nanobot.providers.azure_openai_provider import AzureOpenAIProvider

        provider = AzureOpenAIProvider(
            api_key=p.api_key,
            api_base=p.api_base,
            default_model=model,
        )
    elif backend == "github_copilot":
        from nanobot.providers.github_copilot_provider import GitHubCopilotProvider
        provider = GitHubCopilotProvider(default_model=model)
    elif backend == "anthropic":
        from nanobot.providers.anthropic_provider import AnthropicProvider

        provider = AnthropicProvider(
            api_key=p.api_key if p else None,
            api_base=config.get_api_base(model),
            default_model=model,
            extra_headers=p.extra_headers if p else None,
        )
    else:
        from nanobot.providers.openai_compat_provider import OpenAICompatProvider

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
    from nanobot.config.loader import load_config, resolve_config_env_vars, set_config_path

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

    from nanobot.config.loader import get_config_path

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
    from nanobot.config.paths import get_cron_dir

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
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show nanobot runtime logs"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
):
    """启动 OpenAI 兼容的 API 服务（/v1/chat/completions）。"""
    try:
        from aiohttp import web  # noqa: F401
    except ImportError:
        console.print("[red]aiohttp is required. Install with: pip install 'nanobot-ai[api]'[/red]")
        raise typer.Exit(1)

    from loguru import logger
    from nanobot.agent.loop import AgentLoop
    from nanobot.api.server import create_app
    from nanobot.bus.queue import MessageBus
    from nanobot.session.manager import SessionManager

    if verbose:
        logger.enable("nanobot")
    else:
        logger.disable("nanobot")

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
    """启动 nanobot gateway。"""
    from nanobot.agent.loop import AgentLoop
    from nanobot.bus.queue import MessageBus
    from nanobot.channels.manager import ChannelManager
    from nanobot.cron.service import CronService
    from nanobot.cron.types import CronJob
    from nanobot.heartbeat.service import HeartbeatService
    from nanobot.session.manager import SessionManager

    if verbose:
        import logging

        logging.basicConfig(level=logging.DEBUG)

    config = _load_runtime_config(config, workspace)
    port = port if port is not None else config.gateway.port

    console.print(f"{__logo__} Starting nanobot gateway version {__version__} on port {port}...")
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

        from nanobot.agent.tools.cron import CronTool
        from nanobot.agent.tools.message import MessageTool
        from nanobot.utils.evaluator import evaluate_response

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
                from nanobot.bus.events import OutboundMessage
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
        from nanobot.bus.events import OutboundMessage
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
    from nanobot.cron.types import CronJob, CronPayload
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
    logs: bool = typer.Option(False, "--logs/--no-logs", help="Show nanobot runtime logs during chat"),
    zotero_bridge_host: str = typer.Option("127.0.0.1", "--zotero-bridge-host", help="Zotero bridge bind host"),
    zotero_bridge_port: int = typer.Option(8765, "--zotero-bridge-port", help="Zotero bridge bind port"),
):
    """直接与 agent 交互。"""
    from loguru import logger

    from nanobot.agent.loop import AgentLoop
    from nanobot.bus.queue import MessageBus
    from nanobot.cron.service import CronService

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
        logger.enable("nanobot")
    else:
        logger.disable("nanobot")

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

        asyncio.run(run_once())
    else:
        # 交互模式：像其他 channel 一样通过 bus 路由
        from nanobot.bus.events import InboundMessage
        interactive_console = _has_interactive_console()
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

                    from nanobot.bus.events import InboundMessage
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

                app = web.Application()
                app.router.add_get("/health", _health)
                app.router.add_get("/zotero/history", _history)
                app.router.add_post("/zotero/message", _ingest)
                app.router.add_post("/zotero/stream", _stream_chat)
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
    from nanobot.channels.registry import discover_all
    from nanobot.config.loader import load_config, set_config_path

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
    from nanobot.config.paths import get_bridge_install_dir

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
    pkg_bridge = Path(__file__).parent.parent / "bridge"  # nanobot/bridge（已安装）
    src_bridge = Path(__file__).parent.parent.parent / "bridge"  # 仓库根/bridge（开发态）

    source = None
    if (pkg_bridge / "package.json").exists():
        source = pkg_bridge
    elif (src_bridge / "package.json").exists():
        source = src_bridge

    if not source:
        console.print("[red]Bridge source not found.[/red]")
        console.print("Try reinstalling: pip install --force-reinstall nanobot")
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
    from nanobot.channels.registry import discover_all
    from nanobot.config.loader import load_config, set_config_path

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
    from nanobot.channels.registry import discover_all, discover_channel_names
    from nanobot.config.loader import load_config

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
    """显示 nanobot 状态。"""
    from nanobot.config.loader import get_config_path, load_config

    config_path = get_config_path()
    config = load_config()
    workspace = config.workspace_path

    console.print(f"{__logo__} nanobot Status\n")

    console.print(f"Config: {config_path} {'[green]✓[/green]' if config_path.exists() else '[red]✗[/red]'}")
    console.print(f"Workspace: {workspace} {'[green]✓[/green]' if workspace.exists() else '[red]✗[/red]'}")

    if config_path.exists():
        from nanobot.providers.registry import PROVIDERS

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
    from nanobot.providers.registry import PROVIDERS

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
        from nanobot.providers.github_copilot_provider import login_github_copilot

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
