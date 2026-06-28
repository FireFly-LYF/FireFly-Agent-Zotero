"""Replay zotero_chat-1.jsonl at docx verify checkpoints to diagnose tool batching.

Uses production ContextBuilder flow:
  build prefix → prepare_messages_for_llm (tool 摘要送 LLM)

Compares raw vs LLM-prepared context and isolated baseline.

Usage (from repo root, use the same Python as config tools.mcpServers.docx-mcp):
    D:\\conda_envs\\agent\\python.exe backend/scripts/verify_parallel_tool_calls.py
    D:\\conda_envs\\agent\\python.exe backend/scripts/verify_parallel_tool_calls.py --dry-run
    D:\\conda_envs\\agent\\python.exe backend/scripts/verify_parallel_tool_calls.py --checkpoint after_get_headings
    D:\\conda_envs\\agent\\python.exe backend/scripts/verify_parallel_tool_calls.py --context-mode both
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

REPO_ROOT = Path(__file__).resolve().parents[2]
FIREFLY_ROOT = REPO_ROOT / "backend" / "FireFly"
DEFAULT_CONFIG = REPO_ROOT / "backend" / "config" / "config.json"
DEFAULT_SESSION = REPO_ROOT / "backend" / "workspace" / "sessions" / "zotero_chat-1.jsonl"

if str(FIREFLY_ROOT) not in sys.path:
    sys.path.insert(0, str(FIREFLY_ROOT))

ContextMode = Literal["llm", "raw", "both"]

CHECKPOINTS: dict[str, dict[str, Any]] = {
    "after_open_document": {
        "line_end": 10,
        "iteration": 5,
        "label": "open_document 完成后，下轮实际调用 get_document_info",
        "session_next": ["mcp_docx-mcp_get_document_info"],
    },
    "after_get_document_info": {
        "line_end": 12,
        "iteration": 6,
        "label": "get_document_info 完成后，下轮实际调用 get_headings",
        "session_next": ["mcp_docx-mcp_get_headings"],
    },
    "after_get_headings": {
        "line_end": 14,
        "iteration": 7,
        "label": "get_headings 完成后，下轮实际只调 1 个 search_text（理想应批量 3 个）",
        "session_next": ["mcp_docx-mcp_search_text"],
        "ideal_batch": 3,
    },
    "after_first_search": {
        "line_end": 16,
        "iteration": 8,
        "label": "第 1 个 search_text 完成后，下轮仍只调 1 个 search_text",
        "session_next": ["mcp_docx-mcp_search_text"],
    },
}

ISOLATED_USER_PROMPT = (
    "docx 已打开（handle=__default__）。请查看文档结构，并搜索「双向-双滑窗」「STMF」「信干比改善因子」。"
)


@dataclass
class ToolRound:
    index: int
    line: int
    tool_count: int
    tool_names: list[str]
    content_preview: str = ""


@dataclass
class ContextStats:
    message_count: int
    total_content_chars: int
    tool_content_chars: int
    assistant_tool_step_chars: int


@dataclass
class ApiResult:
    parallel: bool | None
    scenario: str
    checkpoint: str
    context_mode: str
    tool_count: int
    tool_names: list[str]
    finish_reason: str | None
    elapsed_ms: float
    stats: ContextStats | None = None
    error: str | None = None
    tool_schema_count: int = 0


@dataclass
class SessionAnalysis:
    session_path: Path
    rounds: list[ToolRound] = field(default_factory=list)
    verify_rounds: list[ToolRound] = field(default_factory=list)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        obj = json.loads(raw)
        if obj.get("_type") == "metadata":
            continue
        obj["_line"] = line_no
        rows.append(obj)
    return rows


def _strip_session_metadata(msg: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in msg.items() if k not in {"literature_title", "timestamp", "_line"}}


def _tool_names_from_assistant(msg: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        name = fn.get("name")
        if isinstance(name, str):
            names.append(name)
    return names


def analyze_session(rows: list[dict[str, Any]], *, session_path: Path) -> SessionAnalysis:
    analysis = SessionAnalysis(session_path=session_path)
    round_idx = 0
    in_verify = False
    verify_tools = (
        "mcp_docx-mcp_open_document",
        "mcp_docx-mcp_get_document_info",
        "mcp_docx-mcp_get_headings",
        "mcp_docx-mcp_search_text",
        "mcp_docx-mcp_save_document",
    )
    for row in rows:
        if row.get("role") != "assistant" or not row.get("tool_calls"):
            continue
        round_idx += 1
        names = _tool_names_from_assistant(row)
        if any(n == "mcp_docx-mcp_open_document" for n in names):
            in_verify = True
        content = str(row.get("content") or "").strip()
        preview = content[:80] + ("..." if len(content) > 80 else "")
        tr = ToolRound(
            index=round_idx,
            line=int(row.get("_line", 0)),
            tool_count=len(names),
            tool_names=names,
            content_preview=preview or "(empty)",
        )
        analysis.rounds.append(tr)
        if in_verify and any(n in verify_tools for n in names):
            analysis.verify_rounds.append(tr)
    return analysis


def _message_content_chars(msg: dict[str, Any]) -> int:
    content = msg.get("content")
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(len(str(block.get("text") or "")) for block in content if isinstance(block, dict))
    return 0


def _context_stats(messages: list[dict[str, Any]]) -> ContextStats:
    tool_chars = 0
    assistant_tool_chars = 0
    total = 0
    for msg in messages:
        chars = _message_content_chars(msg)
        total += chars
        if msg.get("role") == "tool":
            tool_chars += chars
        elif msg.get("role") == "assistant" and msg.get("tool_calls"):
            assistant_tool_chars += chars
            for tc in msg.get("tool_calls") or []:
                fn = (tc.get("function") if isinstance(tc, dict) else None) or {}
                args = fn.get("arguments")
                if isinstance(args, str):
                    assistant_tool_chars += len(args)
    return ContextStats(
        message_count=len(messages),
        total_content_chars=total,
        tool_content_chars=tool_chars,
        assistant_tool_step_chars=assistant_tool_chars,
    )


def build_agent_messages_from_prefix(
    prefix: list[dict[str, Any]],
    *,
    system_prompt: str,
    channel: str = "zotero",
    chat_id: str = "chat-1",
    timezone: str = "UTC",
    context_mode: ContextMode = "llm",
) -> list[dict[str, Any]]:
    """重建 checkpoint 处送 LLM 的 messages（与 loop + runner 一致）。"""
    from firefly.agent.context import ContextBuilder

    if not prefix:
        raise ValueError("empty prefix")

    raw_prefix = [dict(_strip_session_metadata(m)) for m in prefix]
    first = raw_prefix[0]
    if first.get("role") != "user":
        raise ValueError("session prefix must start with user message")

    user_content = str(first.get("content") or "")
    runtime_ctx = ContextBuilder._build_runtime_context(channel, chat_id, timezone)
    merged_user = {"role": "user", "content": f"{runtime_ctx}\n\n{user_content}"}
    raw_messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        merged_user,
        *raw_prefix[1:],
    ]

    if context_mode == "raw":
        return raw_messages
    return ContextBuilder.prepare_messages_for_llm(raw_messages, channel=channel)


def _quiet_firefly_logs() -> None:
    try:
        from loguru import logger

        logger.disable("firefly")
    except Exception:
        pass


def _summarize_tools(tool_calls: list[Any]) -> list[str]:
    names: list[str] = []
    for tc in tool_calls:
        if hasattr(tc, "name"):
            names.append(str(tc.name))
            continue
        fn = (tc.get("function") if isinstance(tc, dict) else None) or {}
        names.append(str(fn.get("name") or "?"))
    return names


async def _setup_agent(config_path: Path, *, connect_mcp: bool) -> tuple[Any, Any, list[dict[str, Any]], Any]:
    from firefly.agent.loop import AgentLoop
    from firefly.bus.queue import MessageBus
    from firefly.config.loader import load_config, resolve_config_env_vars, set_config_path
    from firefly.zotero_interface.commands import _make_provider

    set_config_path(config_path.resolve())
    config = resolve_config_env_vars(load_config(config_path))
    workspace = (REPO_ROOT / "backend" / config.agents.defaults.workspace).resolve()
    config.agents.defaults.workspace = str(workspace)

    provider = _make_provider(config)
    bus = MessageBus()
    loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=workspace,
        model=config.agents.defaults.model,
        max_iterations=config.agents.defaults.max_tool_iterations,
        context_window_tokens=config.agents.defaults.context_window_tokens,
        context_block_limit=config.agents.defaults.context_block_limit,
        max_tool_result_chars=config.agents.defaults.max_tool_result_chars,
        provider_retry_mode=config.agents.defaults.provider_retry_mode,
        web_config=config.tools.web,
        exec_config=config.tools.exec,
        restrict_to_workspace=config.tools.restrict_to_workspace,
        mcp_servers=config.tools.mcp_servers,
        timezone=config.agents.defaults.timezone,
        disabled_skills=config.agents.defaults.disabled_skills,
    )
    if connect_mcp:
        await loop._connect_mcp()
    system_prompt = loop.context.build_system_prompt(channel="zotero")
    tools = loop.tools.get_definitions()
    return provider, loop, tools, system_prompt


async def _call_provider(
    provider: Any,
    *,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    parallel: bool | None,
    temperature: float,
    max_tokens: int,
    reasoning_effort: str | None,
    stats: ContextStats | None = None,
) -> ApiResult:
    from firefly.providers.openai_compat_provider import OpenAICompatProvider

    t0 = time.perf_counter()
    try:
        if isinstance(provider, OpenAICompatProvider):
            kwargs = provider._build_kwargs(
                messages,
                tools,
                provider.default_model,
                max_tokens,
                temperature,
                reasoning_effort,
                "auto",
            )
            if parallel is not None:
                kwargs["parallel_tool_calls"] = parallel
            raw = await provider._client.chat.completions.create(**kwargs)
            response = provider._parse(raw)
        else:
            response = await provider.chat(
                messages=messages,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
                reasoning_effort=reasoning_effort,
                tool_choice="auto",
            )
        elapsed = (time.perf_counter() - t0) * 1000
        names = _summarize_tools(list(response.tool_calls or []))
        return ApiResult(
            parallel=parallel,
            scenario="",
            checkpoint="",
            context_mode="",
            tool_count=len(names),
            tool_names=names,
            finish_reason=response.finish_reason,
            elapsed_ms=elapsed,
            stats=stats,
            tool_schema_count=len(tools),
        )
    except Exception as exc:  # noqa: BLE001
        elapsed = (time.perf_counter() - t0) * 1000
        return ApiResult(
            parallel=parallel,
            scenario="",
            checkpoint="",
            context_mode="",
            tool_count=0,
            tool_names=[],
            finish_reason="error",
            elapsed_ms=elapsed,
            stats=stats,
            error=f"{type(exc).__name__}: {exc}",
            tool_schema_count=len(tools),
        )


def _isolated_messages(system_prompt: str) -> list[dict[str, Any]]:
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": ISOLATED_USER_PROMPT},
    ]


def _docx_readonly_tools(all_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    names = {
        "mcp_docx-mcp_open_document",
        "mcp_docx-mcp_get_document_info",
        "mcp_docx-mcp_get_headings",
        "mcp_docx-mcp_search_text",
    }
    return [
        schema
        for schema in all_tools
        if str((schema.get("function") or {}).get("name") or "") in names
    ]


def _context_modes(mode: ContextMode) -> list[str]:
    if mode == "both":
        return ["raw", "llm"]
    return [mode]


def print_session_analysis(analysis: SessionAnalysis) -> None:
    print("=" * 72)
    print(f"Session: {analysis.session_path.name}")
    print("=" * 72)
    print("\n## 1. 会话中每轮 assistant 的 tool_calls 数量\n")
    print(f"{'轮次':>4}  {'行号':>4}  {'数量':>4}  工具名")
    print("-" * 72)
    for tr in analysis.rounds:
        names = ", ".join(tr.tool_names) or "(none)"
        flag = "  <-- 验证阶段" if tr in analysis.verify_rounds else ""
        print(f"{tr.index:4d}  {tr.line:4d}  {tr.tool_count:4d}  {names}{flag}")

    batched = [tr for tr in analysis.rounds if tr.tool_count > 1]
    print()
    if batched:
        print(f"会话中存在 {len(batched)} 轮批量 tool_calls。")
    else:
        print("会话中 **每一轮 assistant 消息都只有 1 个 tool_call**。")


def print_context_comparison(raw_stats: ContextStats, llm_stats: ContextStats) -> None:
    def _pct(saved: int, base: int) -> str:
        if base <= 0:
            return "n/a"
        return f"{100 - int(saved * 100 / base)}%"

    print(
        f"  context chars: total {raw_stats.total_content_chars} → {llm_stats.total_content_chars} "
        f"(saved {_pct(llm_stats.total_content_chars, raw_stats.total_content_chars)})"
    )
    print(
        f"  tool chars:    {raw_stats.tool_content_chars} → {llm_stats.tool_content_chars} "
        f"(saved {_pct(llm_stats.tool_content_chars, raw_stats.tool_content_chars)})"
    )


def print_checkpoint_header(name: str, meta: dict[str, Any], *, context_mode: str) -> None:
    print()
    print("-" * 72)
    print(f"Checkpoint: {name}  (iteration≈{meta['iteration']})  context={context_mode}")
    print(f"  {meta['label']}")
    print(f"  会话实际下轮: {', '.join(meta['session_next'])}")
    if ideal := meta.get("ideal_batch"):
        print(f"  理想批量大小: {ideal}")


def print_api_result(result: ApiResult) -> None:
    par = "default" if result.parallel is None else str(result.parallel).lower()
    mode = result.context_mode or "?"
    if result.stats:
        s = result.stats
        print(
            f"  [{mode}] parallel={par}  messages={s.message_count}  "
            f"chars={s.total_content_chars}  tool_chars={s.tool_content_chars}  "
            f"tools={result.tool_schema_count}"
        )
    else:
        print(f"  [{mode}] parallel={par}  tools={result.tool_schema_count}")
    if result.error:
        print(f"  ERROR: {result.error}")
        return
    print(f"  finish_reason={result.finish_reason}  elapsed={result.elapsed_ms:.0f}ms")
    print(f"  tool_calls_count={result.tool_count}")
    for i, name in enumerate(result.tool_names, 1):
        print(f"    [{i}] {name}")
    if result.tool_count >= 2:
        print("  => batched: multiple tool_calls in one response")
    elif result.tool_count == 1:
        print("  => serial: 1 tool_call")
    else:
        print("  => no tool_calls")


def print_diagnosis(
    analysis: SessionAnalysis,
    api_results: list[ApiResult],
) -> None:
    print()
    print("=" * 72)
    print("## 诊断结论")
    print("=" * 72)

    session_single = all(tr.tool_count <= 1 for tr in analysis.rounds)
    replay_llm = [r for r in api_results if r.scenario == "session-replay" and r.context_mode == "llm"]
    replay_raw = [r for r in api_results if r.scenario == "session-replay" and r.context_mode == "raw"]
    isolated = [r for r in api_results if r.scenario == "isolated"]

    llm_multi = any(r.tool_count >= 2 and not r.error for r in replay_llm)
    raw_multi = any(r.tool_count >= 2 and not r.error for r in replay_raw)
    iso_multi = any(r.tool_count >= 2 and not r.error for r in isolated)

    print()
    if session_single:
        print("1. 会话事实：验证阶段每轮 LLM 响应仅含 1 个 tool_call。")
    print("2. 生产路径：ContextBuilder.prepare_messages_for_llm 将 tool 回复摘要送 LLM，session 保留全文。")

    if llm_multi and not raw_multi:
        print("3. tool 摘要 **有效**：llm 上下文可批量，raw 全量上下文仍串行。")
    elif llm_multi and raw_multi:
        print("3. 两种上下文均可批量 — 问题可能已解决或 checkpoint 选取有关。")
    elif not llm_multi and raw_multi:
        print("3. 异常：raw 可批量但 llm 摘要反而串行（检查摘要是否丢失关键信息）。")
    elif not llm_multi and not raw_multi and iso_multi:
        print("3. 即使 raw/llm 会话上下文仍串行；精简 isolated 场景可批量 → 仍有多工具/schema 干扰。")
    elif not llm_multi and not raw_multi:
        print("3. raw 与 llm 上下文均串行；模型在此 checkpoint 倾向逐步调工具。")

    after = [r for r in replay_llm if r.checkpoint == "after_get_headings" and not r.error]
    if after:
        best = max(after, key=lambda r: r.tool_count)
        ideal = CHECKPOINTS["after_get_headings"].get("ideal_batch", 3)
        print()
        print(f"4. after_get_headings (llm context): tool_calls={best.tool_count}，理想≥{ideal}。")


async def async_main(args: argparse.Namespace) -> int:
    session_path = Path(args.session).resolve()
    if not session_path.is_file():
        print(f"Session not found: {session_path}", file=sys.stderr)
        return 2

    rows = _load_jsonl(session_path)
    analysis = analyze_session(rows, session_path=session_path)
    print_session_analysis(analysis)

    if args.dry_run:
        print("\n(--dry-run: 跳过 LLM / MCP 调用)")
        return 0

    config_path = Path(args.config).resolve()
    if not config_path.is_file():
        print(f"Config not found: {config_path}", file=sys.stderr)
        return 2

    _quiet_firefly_logs()
    provider, loop, all_tools, system_prompt = await _setup_agent(
        config_path,
        connect_mcp=not args.skip_mcp,
    )
    try:
        temperature = provider.generation.temperature
        max_tokens = provider.generation.max_tokens
        reasoning = provider.generation.reasoning_effort
        modes = _context_modes(args.context_mode)

        print()
        print("=" * 72)
        print("## 2. Agent 上下文（ContextBuilder.prepare_messages_for_llm）")
        print("=" * 72)
        print(f"model={provider.default_model}")
        print(f"system_prompt_chars={len(system_prompt)}")
        print(f"tool_schemas={len(all_tools)}  MCP connected={not args.skip_mcp}")
        print(f"context_mode={args.context_mode}")

        checkpoint_names = list(CHECKPOINTS.keys()) if args.checkpoint == "all" else [args.checkpoint]
        api_results: list[ApiResult] = []

        for cp_name in checkpoint_names:
            if cp_name not in CHECKPOINTS:
                print(f"Unknown checkpoint: {cp_name}", file=sys.stderr)
                return 2
            meta = CHECKPOINTS[cp_name]
            line_end = int(meta["line_end"])
            prefix = [_strip_session_metadata(r) for r in rows if int(r.get("_line", 0)) <= line_end]

            raw_messages = build_agent_messages_from_prefix(
                prefix,
                system_prompt=system_prompt,
                timezone=loop.context.timezone or "UTC",
                context_mode="raw",
            )
            llm_messages = build_agent_messages_from_prefix(
                prefix,
                system_prompt=system_prompt,
                timezone=loop.context.timezone or "UTC",
                context_mode="llm",
            )
            raw_stats = _context_stats(raw_messages)
            llm_stats = _context_stats(llm_messages)

            if args.context_mode == "both":
                print()
                print("-" * 72)
                print(f"Checkpoint: {cp_name}  context size (raw → llm)")
                print_context_comparison(raw_stats, llm_stats)

            for ctx_mode in modes:
                messages = raw_messages if ctx_mode == "raw" else llm_messages
                stats = raw_stats if ctx_mode == "raw" else llm_stats
                print_checkpoint_header(cp_name, meta, context_mode=ctx_mode)

                for parallel in (True, False):
                    result = await _call_provider(
                        provider,
                        messages=messages,
                        tools=all_tools,
                        parallel=parallel,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        reasoning_effort=reasoning,
                        stats=stats,
                    )
                    result.scenario = "session-replay"
                    result.checkpoint = cp_name
                    result.context_mode = ctx_mode
                    api_results.append(result)
                    print_api_result(result)

        print()
        print("=" * 72)
        print("## 3. 对照：精简 prompt + 仅 docx 只读工具")
        print("=" * 72)
        iso_tools = _docx_readonly_tools(all_tools)
        if not iso_tools:
            print("  (skip: no docx MCP tools — run without --skip-mcp)")
        else:
            iso_messages = _isolated_messages(system_prompt)
            print(f"  tools={len(iso_tools)}")
            for parallel in (True, False):
                result = await _call_provider(
                    provider,
                    messages=iso_messages,
                    tools=iso_tools,
                    parallel=parallel,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    reasoning_effort=reasoning,
                )
                result.scenario = "isolated"
                result.checkpoint = "isolated"
                result.context_mode = "isolated"
                api_results.append(result)
                print_api_result(result)

        print_diagnosis(analysis, api_results)
    finally:
        if not args.skip_mcp:
            await loop.close_mcp()

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Diagnose parallel tool_calls with ContextBuilder LLM context",
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--session", default=str(DEFAULT_SESSION))
    parser.add_argument(
        "--checkpoint",
        default="all",
        choices=[*CHECKPOINTS.keys(), "all"],
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-mcp", action="store_true")
    parser.add_argument(
        "--context-mode",
        default="llm",
        choices=["llm", "raw", "both"],
        help="llm=prepare_messages_for_llm (production); raw=full tool content; both=compare",
    )
    args = parser.parse_args()
    return asyncio.run(async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
