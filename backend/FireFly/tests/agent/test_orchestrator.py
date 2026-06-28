"""Tests for task plan orchestrator."""

from __future__ import annotations

import asyncio

from firefly.agent.orchestrator import TaskOrchestrator


def test_start_plan_sets_active_stage() -> None:
    orch = TaskOrchestrator()
    msg = orch.start_plan(["Literature RAG", "Write docx", "Verify docx"])
    assert "3 stage" in msg
    assert orch.is_active
    assert orch.stages[0].status == "active"
    assert orch.stages[1].status == "pending"


def test_await_stage_waits_for_subtasks() -> None:
    async def _run() -> None:
        orch = TaskOrchestrator()
        orch.start_plan(["Stage A", "Stage B"])
        orch.register_subtask("t1", "job1", "do one")
        orch.register_subtask("t2", "job2", "do two")

        async def complete_later() -> None:
            await asyncio.sleep(0.05)
            orch.complete_subtask("t1", "result one", ok=True)
            await asyncio.sleep(0.05)
            orch.complete_subtask("t2", "result two", ok=True)

        waiter = asyncio.create_task(orch.await_current_stage())
        await complete_later()
        out = await waiter

        assert "Stage 1 complete" in out
        assert "result one" in out
        assert "stage 2/2" in out.lower()
        assert orch.stages[0].status == "completed"
        assert orch.stages[1].status == "active"
        assert not orch.pending

    asyncio.run(_run())


def test_await_stage_advances_without_spawn() -> None:
    async def _run() -> None:
        orch = TaskOrchestrator()
        orch.start_plan(["Inline only", "Next"])
        out = await orch.await_current_stage()
        assert "no subagents" in out
        assert "stage 2" in out.lower()
        assert orch.current_stage_index == 1

    asyncio.run(_run())


def test_format_context_shows_pending_table() -> None:
    orch = TaskOrchestrator()
    orch.start_plan(["Verify"])
    orch.register_subtask("abc", "verify", "check docx")
    block = orch.format_context_block()
    assert block is not None
    assert "[>] 1. Verify" in block
    assert "abc [running]" in block
