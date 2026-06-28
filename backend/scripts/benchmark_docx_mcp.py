"""Benchmark docx-mcp tool call latency (stdio MCP, same path as FireFly).

Individual MCP tools are usually fast (ms–low s). Perceived slowness in Zotero
often comes from LLM round-trips between tools and ~1–2s cold MCP startup.

Usage (repo root):
    python backend/scripts/benchmark_docx_mcp.py
    python backend/scripts/benchmark_docx_mcp.py --config backend/config/config.json
    python backend/scripts/benchmark_docx_mcp.py --docx path/to/file.docx
    python backend/scripts/benchmark_docx_mcp.py --profile agent-verify --estimate-llm-sec 8
    python backend/scripts/benchmark_docx_mcp.py --fixture large --md-paragraphs 50
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import tempfile
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPO_ROOT / "backend" / "config" / "config.json"
FIXTURES = {
    "small": REPO_ROOT / "backend" / "download" / "docx-mcp" / "tests" / "fixtures" / "poi_sample.docx",
    "large": REPO_ROOT / "backend" / "download" / "docx-mcp" / "tests" / "fixtures" / "real_contract.docx",
}


@dataclass
class TimedCall:
    name: str
    seconds: float
    ok: bool
    detail: str = ""

    @property
    def ms(self) -> float:
        return self.seconds * 1000


@dataclass
class BenchmarkReport:
    rows: list[TimedCall] = field(default_factory=list)
    wall_seconds: float = 0.0

    def add(self, name: str, seconds: float, *, ok: bool = True, detail: str = "") -> None:
        self.rows.append(TimedCall(name=name, seconds=seconds, ok=ok, detail=detail))

    def print_table(self) -> None:
        if not self.rows:
            print("(no results)")
            return
        name_w = max(len(r.name) for r in self.rows)
        print(f"{'step':<{name_w}}  {'ms':>8}  status  note")
        print("-" * (name_w + 24))
        for row in self.rows:
            status = "OK" if row.ok else "FAIL"
            note = row.detail[:60] if row.detail else ""
            print(f"{row.name:<{name_w}}  {row.ms:8.1f}  {status:<5}  {note}")
        print(f"{'TOTAL wall clock':<{name_w}}  {self.wall_seconds * 1000:8.1f}")


def _load_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _docx_mcp_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    servers = (cfg.get("tools") or {}).get("mcpServers") or (cfg.get("tools") or {}).get("mcp_servers") or {}
    block = servers.get("docx-mcp")
    if not block:
        raise SystemExit("config.json: tools.mcpServers.docx-mcp not found")
    return block


def _resolve_docx(path: str | None, fixture: str) -> Path:
    if path:
        p = Path(path).expanduser().resolve()
        if not p.is_file():
            raise SystemExit(f"docx not found: {p}")
        return p
    default = FIXTURES.get(fixture) or FIXTURES["small"]
    if not default.is_file():
        raise SystemExit(f"No --docx given and fixture missing: {default}")
    return default


def _sample_markdown(paragraphs: int) -> str:
    lines = ["# Benchmark docx-mcp", "", "## 1. 系统架构", ""]
    for i in range(2, paragraphs + 2):
        lines.extend(
            [
                f"## {i}. 章节 {i}",
                "",
                f"### {i}.1 子节",
                "",
                f"- 步骤 {i}a：初始化参数",
                f"- 步骤 {i}b：执行 Q-learning 更新",
                f"- 奖励函数：基于脉冲压缩第一峰与第二峰差值",
                "",
                "```",
                f"Q(S,A) <- Q(S,A) + eta * [rho + gamma * max Q(S',A') - Q(S,A)]  # section {i}",
                "```",
                "",
            ]
        )
    return "\n".join(lines)


async def _connect_session(mcp_cfg: dict[str, Any]):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    command = mcp_cfg.get("command") or mcp_cfg.get("Command")
    args = mcp_cfg.get("args") or mcp_cfg.get("Args") or []
    env = mcp_cfg.get("env") or mcp_cfg.get("Env") or None
    if not command:
        raise SystemExit("docx-mcp config missing command")

    stack = AsyncExitStack()
    await stack.__aenter__()
    params = StdioServerParameters(command=command, args=args, env=env or None)
    read, write = await stack.enter_async_context(stdio_client(params))
    session = await stack.enter_async_context(ClientSession(read, write))
    return stack, session


async def _call_tool(
    session,
    name: str,
    arguments: dict[str, Any] | None = None,
    *,
    timeout: float,
) -> tuple[Any, float]:
    t0 = time.perf_counter()
    result = await asyncio.wait_for(
        session.call_tool(name, arguments=arguments or {}),
        timeout=timeout,
    )
    elapsed = time.perf_counter() - t0
    return result, elapsed


def _tool_text(result: Any, limit: int = 120) -> str:
    parts: list[str] = []
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    joined = "\n".join(parts) or "(empty)"
    if len(joined) > limit:
        return joined[: limit - 3] + "..."
    return joined


async def _profile_agent_verify(
    session,
    *,
    docx_path: Path,
    tool_timeout: float,
    queries: list[str],
    report: BenchmarkReport,
    prefix: str,
) -> None:
    """Simulate zotero verify: open → headings → N×search (serial, one tool per LLM turn)."""
    flow_start = time.perf_counter()

    _, elapsed = await _call_tool(
        session, "open_document", {"path": str(docx_path)}, timeout=tool_timeout
    )
    report.add(f"{prefix}agent/open_document", elapsed)

    _, elapsed = await _call_tool(session, "get_headings", {}, timeout=tool_timeout)
    report.add(f"{prefix}agent/get_headings", elapsed)

    for q in queries:
        _, elapsed = await _call_tool(
            session,
            "search_text",
            {"query": q, "document_handle": "__default__"},
            timeout=tool_timeout,
        )
        report.add(f"{prefix}agent/search_text({q})", elapsed)

    report.add(f"{prefix}agent/verify MCP only TOTAL", time.perf_counter() - flow_start)


def _print_agent_estimate(report: BenchmarkReport, llm_sec: float) -> None:
    verify_total = next((r for r in report.rows if r.name.endswith("verify MCP only TOTAL")), None)
    search_rows = [r for r in report.rows if "agent/search_text(" in r.name]
    if not verify_total:
        return

    mcp_only = verify_total.seconds
    llm_turns = 1 + 1 + len(search_rows)  # open, headings, each search on its own turn
    estimated = mcp_only + llm_turns * llm_sec
    print()
    print("--- agent-verify estimate (serial LLM turns, like zotero_chat-4) ---")
    print(f"  MCP tool time only     : {mcp_only * 1000:.1f} ms")
    print(f"  Assumed LLM turns      : {llm_turns} x {llm_sec:.1f}s = {llm_turns * llm_sec:.1f}s")
    print(f"  Estimated user wait    : ~{estimated:.1f}s")
    batched_turns = 1 + 1  # open turn + one batched read-only turn
    batched_est = mcp_only + batched_turns * llm_sec
    print(f"  If batched in 2 turns  : ~{batched_est:.1f}s  (save ~{estimated - batched_est:.1f}s)")


async def _run_benchmark(
    args: argparse.Namespace,
    *,
    mcp_cfg: dict[str, Any],
    docx_path: Path,
    tool_timeout: float,
) -> BenchmarkReport:
    report = BenchmarkReport()
    wall_t0 = time.perf_counter()
    stack, session = await _connect_session(mcp_cfg)

    try:
        t0 = time.perf_counter()
        await session.initialize()
        report.add("mcp.initialize (cold start)", time.perf_counter() - t0)

        t0 = time.perf_counter()
        tools = await session.list_tools()
        tool_names = {t.name for t in tools.tools}
        report.add("mcp.list_tools", time.perf_counter() - t0, detail=f"{len(tool_names)} tools")

        queries = args.queries or ["step1", "reward", "Q-learning"]

        if args.profile == "agent-verify":
            await _profile_agent_verify(
                session,
                docx_path=docx_path,
                tool_timeout=tool_timeout,
                queries=queries,
                report=report,
                prefix="",
            )
        else:
            docx_str = str(docx_path)
            for run in range(1, args.repeat + 1):
                prefix = f"run{run}/" if args.repeat > 1 else ""

                result, elapsed = await _call_tool(
                    session, "open_document", {"path": docx_str}, timeout=tool_timeout
                )
                report.add(f"{prefix}open_document", elapsed, detail=_tool_text(result, 80))

                for tool_name in ("get_document_info", "get_headings"):
                    result, elapsed = await _call_tool(
                        session, tool_name, {}, timeout=tool_timeout
                    )
                    report.add(f"{prefix}{tool_name}", elapsed, detail=_tool_text(result, 60))

                serial_start = time.perf_counter()
                for q in queries:
                    result, elapsed = await _call_tool(
                        session,
                        "search_text",
                        {"query": q, "document_handle": "__default__"},
                        timeout=tool_timeout,
                    )
                    report.add(f"{prefix}search_text({q})", elapsed, detail=_tool_text(result, 40))
                report.add(
                    f"{prefix}search_text x{len(queries)} serial TOTAL",
                    time.perf_counter() - serial_start,
                )

                async def _search(q: str) -> tuple[str, float, str]:
                    res, el = await _call_tool(
                        session,
                        "search_text",
                        {"query": q, "document_handle": "__default__"},
                        timeout=tool_timeout,
                    )
                    return q, el, _tool_text(res, 40)

                parallel_start = time.perf_counter()
                parallel_results = await asyncio.gather(*(_search(q) for q in queries))
                for q, el, detail in parallel_results:
                    report.add(f"{prefix}search_text({q}) [parallel leg]", el, detail=detail)
                report.add(
                    f"{prefix}search_text x{len(queries)} parallel TOTAL",
                    time.perf_counter() - parallel_start,
                )

                if not args.skip_write:
                    with tempfile.TemporaryDirectory(prefix="docx-mcp-bench-") as tmp:
                        out = Path(tmp) / "bench_create.docx"
                        md = _sample_markdown(args.md_paragraphs)
                        report.add("(meta) markdown_chars", 0.0, detail=str(len(md)))

                        result, elapsed = await _call_tool(
                            session,
                            "create_from_markdown",
                            {"output_path": str(out), "markdown": md},
                            timeout=tool_timeout,
                        )
                        ok = out.is_file()
                        report.add(
                            f"{prefix}create_from_markdown",
                            elapsed,
                            ok=ok,
                            detail=f"size={out.stat().st_size if ok else 0}B",
                        )

                        if ok and "audit_document" in tool_names:
                            result, elapsed = await _call_tool(
                                session,
                                "audit_document",
                                {"document_handle": "__default__"},
                                timeout=tool_timeout,
                            )
                            report.add(f"{prefix}audit_document", elapsed, detail=_tool_text(result, 60))

        report.wall_seconds = time.perf_counter() - wall_t0
        return report
    finally:
        await stack.aclose()


def _summarize_parallel_vs_serial(report: BenchmarkReport) -> None:
    serial = [r.seconds for r in report.rows if r.name.endswith("serial TOTAL")]
    parallel = [r.seconds for r in report.rows if r.name.endswith("parallel TOTAL")]
    if not serial or not parallel:
        return
    s_mean = statistics.mean(serial) * 1000
    p_mean = statistics.mean(parallel) * 1000
    print()
    print("--- search_text x3 summary ---")
    print(f"  serial   mean: {s_mean:.1f} ms  (n={len(serial)})")
    print(f"  parallel mean: {p_mean:.1f} ms  (n={len(parallel)})")
    if p_mean < s_mean:
        print(f"  speedup: {s_mean / p_mean:.2f}x")
    else:
        print("  note: parallel not faster — MCP stdio may serialize requests on one session")


async def _async_main(args: argparse.Namespace) -> int:
    cfg = _load_config(Path(args.config))
    mcp_cfg = _docx_mcp_cfg(cfg)
    docx_path = _resolve_docx(args.docx, args.fixture)
    tool_timeout = float(mcp_cfg.get("toolTimeout") or mcp_cfg.get("tool_timeout") or 120)

    print("docx-mcp benchmark")
    print(f"  config : {Path(args.config).resolve()}")
    print(f"  command: {mcp_cfg.get('command')} {' '.join(mcp_cfg.get('args') or [])}")
    print(f"  docx   : {docx_path} ({docx_path.stat().st_size // 1024} KiB)")
    print(f"  profile: {args.profile}")
    print(f"  timeout: {tool_timeout}s per tool")
    if args.profile == "full":
        print(f"  repeat : {args.repeat}")
    print()

    report = await _run_benchmark(
        args,
        mcp_cfg=mcp_cfg,
        docx_path=docx_path,
        tool_timeout=tool_timeout,
    )
    report.print_table()
    if args.profile == "full":
        _summarize_parallel_vs_serial(report)
    if args.profile == "agent-verify":
        _print_agent_estimate(report, args.estimate_llm_sec)

    init_ms = next((r.ms for r in report.rows if "initialize" in r.name), 0)
    if init_ms > 500:
        print()
        print(f"  tip: mcp.initialize ~{init_ms:.0f}ms — keep bridge running to avoid cold start")

    failed = [r for r in report.rows if not r.ok]
    if failed:
        print(f"\n{len(failed)} step(s) failed", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark docx-mcp tool call latency")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="FireFly config.json path")
    parser.add_argument("--docx", default=None, help="DOCX to open")
    parser.add_argument(
        "--fixture",
        choices=sorted(FIXTURES),
        default="small",
        help="Default fixture when --docx omitted",
    )
    parser.add_argument(
        "--profile",
        choices=("full", "agent-verify"),
        default="full",
        help="full=all tools; agent-verify=open+headings+N×search serial",
    )
    parser.add_argument(
        "--queries",
        nargs="+",
        default=None,
        help="search_text queries (default: step1 reward Q-learning)",
    )
    parser.add_argument("--repeat", type=int, default=1, help="Repeat read benchmark N times")
    parser.add_argument("--skip-write", action="store_true", help="Skip create_from_markdown / audit")
    parser.add_argument("--md-paragraphs", type=int, default=8, help="Sections for create_from_markdown")
    parser.add_argument(
        "--estimate-llm-sec",
        type=float,
        default=8.0,
        help="Assumed seconds per LLM turn (agent-verify profile)",
    )
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat must be >= 1")
    return asyncio.run(_async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
