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
from firefly.skills.markdown.scripts.rag_paths import ensure_markdown_rag_index
from firefly.skills.wiki.scripts.llm_wiki_paths import resolve_wiki_mirror_from_raw_pdf_path


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
from firefly.config.paths import get_workspace_path, get_workspace_temp_dir, is_default_workspace
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

# Zotero 插件 SSE：多轮 tool + 总结 agent 可能长时间无 delta，需更长空闲超时与 keepalive。
ZOTERO_STREAM_IDLE_TIMEOUT_S = 900.0
ZOTERO_STREAM_KEEPALIVE_INTERVAL_S = 25.0

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


def _literature_title_from_bridge_body(body: dict[str, Any] | None) -> str | None:
    """从 Zotero bridge POST JSON 解析当前文献题名（写入会话记录，不进入 LLM）。"""
    if not body:
        return None
    for key in ("literature_title", "literatureTitle", "item_title", "itemTitle"):
        raw = body.get(key)
        if raw is None:
            continue
        s = str(raw).strip()
        if s:
            return s
    return None


def _zotero_append_query_reminder(patched: str, original_user_text: str) -> str:
    """在 RAG/条目上下文注入之后，于末尾再次强调用户原问句，减轻长历史与长 RAG 前块对短问题的压制。"""
    o = (original_user_text or "").strip()
    if not o or len(o) > 4000:
        return patched
    if "【请直接回答此问" in patched:
        return patched
    return f"{patched.rstrip()}\n\n----\n【请直接回答此问（优先于旧对话）】\n{o}\n"


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

    from firefly.config.cli_prefs import load_cli_prefs
    from firefly.config.loader import get_config_path

    show_llm_input = bool(load_cli_prefs(get_config_path()).get("show_llm_input"))
    _llm_input_use_interactive: list[bool] = [True]
    on_llm_request_cb = None

    if show_llm_input:
        async def _on_llm_request_display(payload: dict[str, Any]) -> None:
            """展示发给 LLM 的快照；任意异常不得阻断推理。"""
            title = "LLM 请求快照（默认整段脱敏；可在 user.json 的 llm_input_print 中分段打印）"
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

        on_llm_request_cb = _on_llm_request_display

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
        on_llm_request=on_llm_request_cb,
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

            def _extract_current_wiki_pdf_path(content: str) -> str:
                if not content:
                    return ""
                m = _WIKI_PDF_MARKER_RE.search(content)
                return str(m.group(1)).strip() if m else ""

            def _inject_wiki_markdown_path_marker(content: str) -> str:
                """在已有 wiki PDF marker 时补充对应的 raw/markdown 镜像路径。"""
                if not content or "[zotero_current_wiki_markdown_path=" in content:
                    return content
                pdf_path = _extract_current_wiki_pdf_path(content)
                if not pdf_path:
                    return content
                from firefly.skills.markdown.scripts.rag_paths import (
                    llm_wiki_markdown_mirror_for_pdf,
                    resolve_markdown_path_from_pdf,
                )

                md = llm_wiki_markdown_mirror_for_pdf(Path(pdf_path))
                m = _WIKI_PDF_MARKER_RE.search(content)
                insert_at = m.end() if m else 0
                if md is not None:
                    marker = f"[zotero_current_wiki_markdown_path={md}]"
                    if marker in content:
                        return content
                    block = (
                        f"\n{marker}\n"
                        "[zotero_literature_read_hint] Full-text: grep the markdown path above "
                        "to locate sections (headings/keywords), then read_file with offset from "
                        "grep line numbers — do NOT guess offset. "
                        "Do NOT read_file the PDF when this marker is present."
                    )
                    if m:
                        return content[:insert_at] + block + content[insert_at:]
                    return f"{block}\n\n{content}"
                expected_md = resolve_markdown_path_from_pdf(Path(pdf_path))
                missing = (
                    f"\n[zotero_markdown_mirror_missing] Expected markdown at: {expected_md}\n"
                    "[zotero_literature_read_hint] No .md mirror yet — run PDF→markdown conversion, "
                    "then read_file that path. PDF read is a slow fallback only."
                )
                if m:
                    return content[:insert_at] + missing + content[insert_at:]
                return f"{missing}\n\n{content}"

            _MAX_WIKI_CONTEXT_CHARS = 32000

            def _inject_wiki_page_context(content: str) -> str:
                """若当前文献已有镜像 wiki 页，将其插在条目提示之后。"""
                if not content or "[Wiki Page Context]" in content:
                    return content
                pdf_path = _extract_current_wiki_pdf_path(content)
                if not pdf_path:
                    return content
                resolved = resolve_wiki_mirror_from_raw_pdf_path(pdf_path)
                if not resolved:
                    return content
                wiki_root, wiki_path = resolved
                try:
                    wiki_resolved = wiki_path.expanduser().resolve()
                except OSError:
                    wiki_resolved = wiki_path.expanduser()
                if not wiki_resolved.is_file():
                    return content
                try:
                    body = wiki_resolved.read_text(encoding="utf-8", errors="replace")
                except OSError as e:
                    logger.warning("wiki context: cannot read {} ({})", wiki_resolved, e)
                    return content
                truncated = False
                if len(body) > _MAX_WIKI_CONTEXT_CHARS:
                    body = body[:_MAX_WIKI_CONTEXT_CHARS] + "\n\n…（正文过长已截断）\n"
                    truncated = True
                try:
                    wr = wiki_root.expanduser().resolve()
                    rel_display = wiki_resolved.relative_to(wr).as_posix()
                except Exception:
                    rel_display = str(wiki_resolved)
                block_lines = [
                    "[Wiki Page Context]",
                    "该条目在 llm-wiki 下已有整理页（与 raw/markdown 目录镜像；规范见 AGENTS.md）。"
                    "可作主题结构与要点导航；具体数值与实验细节请用 rag_search 或原文核对。",
                    f"path（相对 llm-wiki 根）: {rel_display}",
                ]
                if truncated:
                    block_lines.append("（正文已按长度上限截断）")
                block_lines.extend(["---", body.rstrip(), "[/Wiki Page Context]"])
                return f"{chr(10).join(block_lines)}\n\n{content}"

            def _cache_zotero_media(media_paths: list[str]) -> list[str]:
                """把前端传来的图片缓存到 workspace/temp，返回可读路径列表。"""
                if not media_paths:
                    return []
                temp_dir = get_workspace_temp_dir(config.workspace_path)
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
                def _serialize_session_messages(session_key: str) -> list[dict[str, Any]]:
                    from firefly.zotero_interface.utils import (
                        is_zotero_user_visible_message,
                        strip_zotero_user_content_for_session_storage,
                    )

                    session = agent_loop.sessions.get_or_create(session_key)
                    out: list[dict[str, Any]] = []
                    for idx, msg in enumerate(session.messages):
                        if not is_zotero_user_visible_message(msg):
                            continue
                        role = str(msg.get("role", ""))
                        content = str(msg.get("content", "") or "")
                        if role == "user":
                            content = strip_zotero_user_content_for_session_storage(content)
                        reasoning = str(msg.get("reasoning_content", "") or "")
                        lit = msg.get("literature_title")
                        lit_s = str(lit).strip() if lit is not None else ""
                        row: dict[str, Any] = {
                            "role": role,
                            "content": content,
                            "reasoning_content": reasoning,
                            "session_message_index": idx,
                        }
                        if lit_s:
                            row["literature_title"] = lit_s
                        ts = msg.get("timestamp")
                        if ts is not None:
                            row["timestamp"] = str(ts)
                        out.append(row)
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

                async def _ensure_markdown_rag_index(markdown_path: Path) -> dict[str, Any]:
                    return await asyncio.to_thread(ensure_markdown_rag_index, markdown_path)

                _PDF_CONVERT_TIMEOUT_SEC = 1800
                _pdf_convert_lock = asyncio.Lock()

                async def _ensure_pdf_converted(
                    pdf_path: Path,
                    markdown_path: Path,
                    *,
                    force_markdown: bool = False,
                    force_ocr: bool = False,
                    use_ocr: bool = False,
                    write_images: bool = True,
                ) -> dict[str, Any]:
                    try:
                        from firefly.skills.markdown.scripts.pdf_to_markdown import (
                            _assets_look_fragmented,
                            assets_dir_path,
                            convert_one as _pdf_to_md,
                        )
                    except SystemExit as exc:
                        raise RuntimeError(
                            "缺少 pymupdf4llm。请在 FireFly 环境中执行："
                            " pip install pymupdf4llm"
                        ) from exc
                    except ImportError as exc:
                        raise RuntimeError(
                            "缺少 pymupdf4llm。请在 FireFly 环境中执行："
                            " pip install pymupdf4llm"
                        ) from exc

                    had_markdown = markdown_path.is_file()
                    assets_dir = assets_dir_path(markdown_path)
                    needs_images = write_images and (
                        not assets_dir.is_dir()
                        or not any(assets_dir.iterdir())
                        or _assets_look_fragmented(assets_dir)
                    )
                    if not force_markdown and had_markdown and not needs_images:
                        return {
                            "converted": False,
                            "reason": "already_converted",
                        }

                    try:
                        markdown_path.parent.mkdir(parents=True, exist_ok=True)
                    except OSError as exc:
                        raise RuntimeError(
                            f"cannot create markdown output directory: {exc}"
                        ) from exc

                    logger.info(
                        "pdf_to_markdown start: {} -> {} (use_ocr={}, force_ocr={}, write_images={})",
                        pdf_path,
                        markdown_path,
                        use_ocr,
                        force_ocr,
                        write_images,
                    )
                    try:
                        pdf_size = pdf_path.stat().st_size
                    except OSError:
                        pdf_size = -1
                    logger.info("pdf_to_markdown input size={} bytes", pdf_size)

                    def _run_convert() -> dict[str, object]:
                        return _pdf_to_md(
                            pdf_path,
                            markdown_path,
                            overwrite=force_markdown,
                            force_ocr=force_ocr,
                            ocr_language="chi_sim+eng",
                            use_ocr=use_ocr,
                            write_images=write_images,
                        )

                    try:
                        async with _pdf_convert_lock:
                            conv = await asyncio.wait_for(
                                asyncio.to_thread(_run_convert),
                                timeout=_PDF_CONVERT_TIMEOUT_SEC,
                            )
                    except asyncio.TimeoutError as exc:
                        raise RuntimeError(
                            f"pdf convert timed out after {_PDF_CONVERT_TIMEOUT_SEC}s: {pdf_path}"
                        ) from exc
                    except Exception as exc:
                        logger.exception(
                            "pdf_to_markdown failed: {} -> {}",
                            pdf_path,
                            markdown_path,
                        )
                        raise RuntimeError(f"pdf convert failed: {exc}") from exc

                    if not markdown_path.is_file():
                        raise RuntimeError(
                            f"pdf convert reported success but markdown missing: {markdown_path}"
                        )

                    status = str(conv.get("status") or "").strip()
                    if status == "skipped":
                        return {
                            "converted": False,
                            "reason": "already_converted",
                        }
                    if status != "converted":
                        raise RuntimeError(
                            f"pdf convert unexpected status={status!r}: {conv}"
                        )

                    logger.info("pdf_to_markdown done: {}", markdown_path)
                    return {
                        "converted": True,
                        "reason": "markdown_regenerated"
                        if (force_markdown and had_markdown)
                        else "converted",
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
                        markdown_path = markdown_path.expanduser().resolve()
                    except Exception:
                        markdown_path = markdown_path.expanduser()
                    try:
                        markdown_path.parent.mkdir(parents=True, exist_ok=True)
                    except OSError as exc:
                        return web.json_response(
                            {
                                "ok": False,
                                "error": f"cannot create markdown parent dir: {exc}",
                                "markdown_path": str(markdown_path),
                            },
                            status=500,
                        )
                    logger.info(
                        "convert-markdown: pdf={} markdown={}",
                        pdf_path,
                        markdown_path,
                    )
                    # force_markdown：即使已有 .md 也从 PDF 重转（传 pdf_to_markdown --overwrite）。
                    # 未指定时保持旧行为：有 md 则跳过 PDF，仅重建 RAG。
                    raw_body = body or {}
                    force_markdown = bool(
                        raw_body.get("force_markdown") or raw_body.get("forceMarkdown")
                    )
                    force_ocr = bool(raw_body.get("force_ocr") or raw_body.get("forceOcr"))
                    use_ocr = bool(raw_body.get("use_ocr") or raw_body.get("useOcr") or force_ocr)
                    if "write_images" in raw_body or "writeImages" in raw_body:
                        write_images = bool(
                            raw_body.get("write_images") or raw_body.get("writeImages")
                        )
                    else:
                        write_images = True
                    try:
                        result = await _ensure_pdf_converted(
                            pdf_path,
                            markdown_path,
                            force_markdown=force_markdown,
                            force_ocr=force_ocr,
                            use_ocr=use_ocr,
                            write_images=write_images,
                        )
                    except Exception as exc:
                        err_log = markdown_path.with_suffix(".md.convert-error.txt")
                        return web.json_response(
                            {
                                "ok": False,
                                "error": str(exc),
                                "pdf_path": str(pdf_path),
                                "markdown_path": str(markdown_path),
                                "error_log": str(err_log) if err_log.is_file() else None,
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
                    patched = _inject_wiki_markdown_path_marker(patched)
                    patched = _inject_wiki_page_context(patched)
                    patched = _zotero_append_query_reminder(patched, content)
                    lit = _literature_title_from_bridge_body(body)
                    await inbound_from_zotero.put({
                        "message": patched,
                        "session_id": override,
                        "media": cached_media,
                        **({"literature_title": lit} if lit else {}),
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
                    patched_content = _inject_wiki_markdown_path_marker(patched_content)
                    patched_content = _inject_wiki_page_context(patched_content)
                    patched_content = _zotero_append_query_reminder(patched_content, content)
                    stream_lit = _literature_title_from_bridge_body(body)

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

                    write_lock = asyncio.Lock()
                    stream_done = asyncio.Event()

                    async def _write_event(event: dict[str, str]) -> None:
                        packet = f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8")
                        async with write_lock:
                            await response.write(packet)

                    async def _keepalive_ping() -> None:
                        while not stream_done.is_set():
                            try:
                                await asyncio.wait_for(
                                    stream_done.wait(),
                                    timeout=ZOTERO_STREAM_KEEPALIVE_INTERVAL_S,
                                )
                                break
                            except asyncio.TimeoutError:
                                if stream_done.is_set():
                                    break
                                await _write_event({"type": "ping"})

                    from firefly.bus.events import InboundMessage
                    stream_meta: dict[str, Any] = {
                        "_wants_stream": True,
                        "_source": "zotero_stream",
                    }
                    if stream_lit:
                        stream_meta["literature_title"] = stream_lit
                    await bus.publish_inbound(InboundMessage(
                        channel=current_channel,
                        sender_id="user",
                        chat_id=current_chat_id,
                        content=patched_content,
                        media=cached_media,
                        metadata=stream_meta,
                    ))

                    ping_task: asyncio.Task[None] | None = None
                    try:
                        await _write_event({"type": "start"})
                        ping_task = asyncio.create_task(_keepalive_ping())
                        while True:
                            event = await asyncio.wait_for(
                                subscriber.get(),
                                timeout=ZOTERO_STREAM_IDLE_TIMEOUT_S,
                            )
                            await _write_event(event)
                            # "end" 仅表示一个流片段结束（可能随后还有最终 final）
                            # 仅在 final/error 时关闭 SSE，避免丢失 thinking/最终正文。
                            if event.get("type") in {"final", "error"}:
                                break
                    except asyncio.TimeoutError:
                        logger.warning(
                            "Zotero stream idle timeout after {}s for chat {}",
                            ZOTERO_STREAM_IDLE_TIMEOUT_S,
                            current_chat_id,
                        )
                        await _write_event({
                            "type": "error",
                            "message": (
                                f"stream timeout: no agent output for "
                                f"{int(ZOTERO_STREAM_IDLE_TIMEOUT_S)}s"
                            ),
                        })
                    finally:
                        stream_done.set()
                        if ping_task is not None:
                            ping_task.cancel()
                            await asyncio.gather(ping_task, return_exceptions=True)
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

                async def _wiki_ingest_from_markdown(request: web.Request) -> web.Response:
                    """Build ``wiki/<mirror>.md`` from the current item's ``raw/markdown`` file (same payload as convert)."""
                    try:
                        body = await request.json()
                    except Exception:
                        body = {}
                    pdf_path = _pick_source_pdf_for_conversion(body or {})
                    if pdf_path is None:
                        return web.json_response(
                            {
                                "ok": False,
                                "detail": "no_pdf",
                                "markdown_path": None,
                                "wiki_path": None,
                            },
                        )
                    markdown_path = _markdown_output_path(body or {}, pdf_path)
                    try:
                        markdown_path = markdown_path.expanduser().resolve()
                    except Exception:
                        markdown_path = markdown_path.expanduser()
                    if not markdown_path.is_file():
                        return web.json_response(
                            {
                                "ok": False,
                                "detail": "markdown_missing",
                                "markdown_path": str(markdown_path),
                                "wiki_path": None,
                            },
                        )
                    from firefly.skills.wiki.scripts.wiki_markdown_ingest import (
                        run_wiki_ingest_from_markdown,
                    )

                    result = await run_wiki_ingest_from_markdown(agent_loop, markdown_path)
                    return web.json_response(result)

                async def _delete_session_turn(request: web.Request) -> web.Response:
                    try:
                        body = await request.json()
                    except Exception:
                        body = {}
                    raw_sid = str((body or {}).get("session_id", "")).strip() or session_id
                    if not str(raw_sid).strip():
                        return web.json_response(
                            {"ok": False, "detail": "session_id is required"},
                            status=400,
                        )
                    try:
                        assistant_index = int((body or {}).get("assistant_message_index", -1))
                    except (TypeError, ValueError):
                        return web.json_response(
                            {"ok": False, "detail": "assistant_message_index must be an integer"},
                            status=400,
                        )
                    target_key = _resolve_zotero_chat_session_key(raw_sid)
                    sess = agent_loop.sessions.get_or_create(target_key)
                    if not sess.delete_turn_containing_assistant_at(assistant_index):
                        return web.json_response(
                            {"ok": False, "detail": "invalid_assistant_message_index"},
                            status=400,
                        )
                    agent_loop.sessions.save(sess)
                    return web.json_response({"ok": True, "detail": "deleted"})

                async def _get_settings(_request: web.Request) -> web.Response:
                    from firefly.zotero_interface.settings_store import read_settings_bundle

                    return web.json_response(read_settings_bundle())

                async def _put_settings(request: web.Request) -> web.Response:
                    from firefly.zotero_interface.settings_store import write_settings_bundle

                    try:
                        body = await request.json()
                    except Exception:
                        body = {}
                    if not isinstance(body, dict):
                        return web.json_response(
                            {"ok": False, "error": "body must be a JSON object"},
                            status=400,
                        )
                    result = write_settings_bundle(body)
                    status = 200 if result.get("ok") else 400
                    return web.json_response(result, status=status)

                app = web.Application()
                app.router.add_get("/health", _health)
                app.router.add_get("/zotero/meta", _meta)
                app.router.add_get("/zotero/history", _history)
                app.router.add_post("/zotero/message", _ingest)
                app.router.add_post("/zotero/stream", _stream_chat)
                app.router.add_post("/zotero/wiki-ingest-markdown", _wiki_ingest_from_markdown)
                app.router.add_post("/zotero/session/delete-turn", _delete_session_turn)
                app.router.add_post("/zotero/cancel", _cancel_chat)
                app.router.add_post("/zotero/session/clear", _clear_session)
                app.router.add_post("/zotero/pdf-opened", _pdf_opened)
                app.router.add_post("/zotero/convert-markdown", _convert_markdown)
                app.router.add_get("/zotero/settings", _get_settings)
                app.router.add_put("/zotero/settings", _put_settings)
                app.router.add_post("/zotero/settings", _put_settings)
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
                "\"literature_title\": \"optional (Zotero 当前文献)\"}[/dim]",
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
                            await _stream_publish(
                                msg.chat_id,
                                {
                                    "type": "end",
                                    "resuming": "true" if msg.metadata.get("_resuming") else "false",
                                },
                            )
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
                            # Zotero 插件 SSE：始终推送工具步骤，不受 channels.send_tool_hints 限制
                            if is_tool_hint:
                                hint = str(msg.content or "").strip()
                                if hint:
                                    await _stream_publish(
                                        msg.chat_id,
                                        {"type": "tool_step", "content": hint},
                                    )
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
                        source_lit: str | None = None
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
                                source_lit = _literature_title_from_bridge_body(payload)
                                await _print_interactive_line(f"[Zotero] {user_input}")
                            else:
                                user_input = input_task.result()
                                source_media = []
                                source_lit = None
                        else:
                            payload = await inbound_from_zotero.get()
                            user_input = payload["message"]
                            source_session = payload.get("session_id") or session_id
                            source_media = payload.get("media") if isinstance(payload.get("media"), list) else []
                            source_lit = _literature_title_from_bridge_body(payload)
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
                        patched_command = _inject_wiki_markdown_path_marker(patched_command)
                        patched_command = _inject_wiki_page_context(patched_command)
                        patched_command = _zotero_append_query_reminder(patched_command, command)

                        inbound_meta: dict[str, Any] = {"_wants_stream": True, "_source": source}
                        if source_lit:
                            inbound_meta["literature_title"] = source_lit
                        await bus.publish_inbound(InboundMessage(
                            channel=current_channel,
                            sender_id="user",
                            chat_id=current_chat_id,
                            content=patched_command,
                            media=[str(p).strip() for p in source_media if str(p).strip()],
                            metadata=inbound_meta,
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
