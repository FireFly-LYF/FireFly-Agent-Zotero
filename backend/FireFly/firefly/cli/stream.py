"""用于 CLI 输出的流式渲染器。

使用 Rich 的 Live（auto_refresh=False）来在流式输出时稳定、无闪烁地渲染 Markdown。
当内容溢出时使用省略模式处理。
"""

from __future__ import annotations

import sys
import time

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.text import Text

from firefly import __logo__


def _make_console() -> Console:
    return Console(file=sys.stdout, force_terminal=True)


class ThinkingSpinner:
    """显示“firefly is thinking...”的转圈提示，支持暂停。"""

    def __init__(self, console: Console | None = None):
        c = console or _make_console()
        self._spinner = c.status("[dim]firefly is thinking...[/dim]", spinner="dots")
        self._active = False

    def __enter__(self):
        self._spinner.start()
        self._active = True
        return self

    def __exit__(self, *exc):
        self._active = False
        self._spinner.stop()
        return False

    def pause(self):
        """上下文管理器：临时停止转圈，避免输出内容被打断。"""
        from contextlib import contextmanager

        @contextmanager
        def _ctx():
            if self._spinner and self._active:
                self._spinner.stop()
            try:
                yield
            finally:
                if self._spinner and self._active:
                    self._spinner.start()

        return _ctx()


class StreamRenderer:
    """基于 Rich Live 的流式渲染（支持 Markdown）。auto_refresh=False 用于避免渲染竞争导致闪烁。

    从 agent loop 收到的增量内容（delta）已预先过滤（不含 <think> 标签）。

    每一轮的流程：
      spinner -> 第一个可见 delta -> 输出标题 + Live 渲染 ->
      on_end -> Live 停止（内容保留在屏幕上）
    """

    def __init__(self, render_markdown: bool = True, show_spinner: bool = True):
        self._md = render_markdown
        self._show_spinner = show_spinner
        self._buf = ""
        self._live: Live | None = None
        self._t = 0.0
        self.streamed = False
        self._spinner: ThinkingSpinner | None = None
        self._start_spinner()

    def _render(self):
        return Markdown(self._buf) if self._md and self._buf else Text(self._buf or "")

    def _start_spinner(self) -> None:
        if self._show_spinner:
            self._spinner = ThinkingSpinner()
            self._spinner.__enter__()

    def _stop_spinner(self) -> None:
        if self._spinner:
            self._spinner.__exit__(None, None, None)
            self._spinner = None

    async def on_delta(self, delta: str) -> None:
        self.streamed = True
        self._buf += delta
        if self._live is None:
            if not self._buf.strip():
                return
            self._stop_spinner()
            c = _make_console()
            c.print()
            c.print(f"[cyan]{__logo__}[/cyan]")
            self._live = Live(self._render(), console=c, auto_refresh=False)
            self._live.start()
        now = time.monotonic()
        if "\n" in delta or (now - self._t) > 0.05:
            self._live.update(self._render())
            self._live.refresh()
            self._t = now

    async def on_end(self, *, resuming: bool = False) -> None:
        if self._live:
            self._live.update(self._render())
            self._live.refresh()
            self._live.stop()
            self._live = None
        self._stop_spinner()
        if resuming:
            self._buf = ""
            self._start_spinner()
        else:
            _make_console().print()

    def stop_for_input(self) -> None:
        """在等待用户输入前停止 spinner，避免与 prompt_toolkit 冲突。"""
        self._stop_spinner()

    async def close(self) -> None:
        """停止 spinner/live，但不强制渲染最后一轮流式输出。"""
        if self._live:
            self._live.stop()
            self._live = None
        self._stop_spinner()
