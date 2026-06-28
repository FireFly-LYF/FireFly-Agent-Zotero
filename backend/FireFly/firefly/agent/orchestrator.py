"""会话级任务编排：todo 阶段 + pending 子任务表。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Literal

StageStatus = Literal["pending", "active", "completed"]
SubtaskStatus = Literal["running", "ok", "error"]


@dataclass
class Stage:
    index: int
    description: str
    status: StageStatus = "pending"


@dataclass
class PendingSubtask:
    task_id: str
    label: str
    task: str
    status: SubtaskStatus = "running"
    result: str = ""


DEFAULT_STAGE_RESULT_PREVIEW_CHARS = 16_000


@dataclass
class TaskOrchestrator:
    """单会话 todo 编排：每阶段 spawn 子任务，await_stage 全部完成后再推进。"""

    stages: list[Stage] = field(default_factory=list)
    current_stage_index: int = -1
    pending: dict[str, PendingSubtask] = field(default_factory=dict)
    completed_stage_results: list[str] = field(default_factory=list)
    result_preview_chars: int = DEFAULT_STAGE_RESULT_PREVIEW_CHARS
    _wake: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def is_active(self) -> bool:
        return self.current_stage_index >= 0 and self.current_stage_index < len(self.stages)

    @property
    def is_complete(self) -> bool:
        return (
            self.current_stage_index >= 0
            and self.current_stage_index >= len(self.stages)
            and not self.running_count()
        )

    def start_plan(self, stage_descriptions: list[str]) -> str:
        cleaned = [s.strip() for s in stage_descriptions if s and s.strip()]
        if not cleaned:
            return "Error: `stages` must contain at least one non-empty description."

        self.stages = [
            Stage(index=i, description=desc, status="active" if i == 0 else "pending")
            for i, desc in enumerate(cleaned)
        ]
        self.current_stage_index = 0
        self.pending.clear()
        self.completed_stage_results.clear()
        self._wake.set()
        return self._format_plan_started()

    def register_subtask(self, task_id: str, label: str, task: str) -> None:
        if not self.is_active:
            return
        self.pending[task_id] = PendingSubtask(task_id=task_id, label=label, task=task)
        self._wake.clear()

    def complete_subtask(self, task_id: str, result: str, *, ok: bool) -> None:
        entry = self.pending.get(task_id)
        if entry is None:
            return
        entry.status = "ok" if ok else "error"
        entry.result = result
        if self.running_count() == 0:
            self._wake.set()

    def running_count(self) -> int:
        return sum(1 for p in self.pending.values() if p.status == "running")

    async def await_current_stage(self) -> str:
        if not self.is_active:
            if self.is_complete:
                return "All stages are already complete."
            return "Error: no active task plan. Call `plan_tasks` first."

        while self.running_count() > 0:
            self._wake.clear()
            await self._wake.wait()

        finished = list(self.pending.values())
        self.pending.clear()

        stage = self.stages[self.current_stage_index]
        stage.status = "completed"
        lines = [f"Stage {stage.index + 1} complete: {stage.description}", ""]

        if finished:
            full_stage_parts: list[str] = []
            lines.append("Subtask results:")
            preview_limit = max(0, self.result_preview_chars)
            for p in finished:
                mark = "ok" if p.status == "ok" else "error"
                lines.append(f"- [{p.task_id}] {p.label} [{mark}]")
                body = p.result.strip()
                if body:
                    full_stage_parts.append(f"[{p.task_id}] {p.label} [{mark}]\n{body}")
                preview = body
                if preview_limit and len(preview) > preview_limit:
                    preview = preview[: preview_limit - 3] + "..."
                if preview:
                    lines.append(f"  {preview}")
            if full_stage_parts:
                self.completed_stage_results.append("\n\n".join(full_stage_parts))
            lines.append("")
        else:
            lines.append("(no subagents were spawned for this stage)")
            lines.append("")

        self.current_stage_index += 1
        if self.current_stage_index < len(self.stages):
            nxt = self.stages[self.current_stage_index]
            nxt.status = "active"
            lines.append(
                f"Proceed to stage {nxt.index + 1}/{len(self.stages)}: {nxt.description}"
            )
            lines.append("Spawn subagents for this stage when ready.")
        else:
            lines.append("All stages complete. Summarize outcomes for the user.")

        return "\n".join(lines)

    def format_context_block(self) -> str | None:
        if self.current_stage_index < 0:
            return None
        lines = ["Task plan:"]
        for stage in self.stages:
            marker = {"pending": "[ ]", "active": "[>]", "completed": "[x]"}[stage.status]
            lines.append(f"  {marker} {stage.index + 1}. {stage.description}")

        if self.is_active:
            cur = self.stages[self.current_stage_index]
            lines.append("")
            lines.append(f"Active stage: {cur.index + 1} — {cur.description}")

        if self.pending:
            lines.append("")
            lines.append("Pending subtasks:")
            for p in self.pending.values():
                lines.append(f"  - {p.task_id} [{p.status}] {p.label}")

        running = self.running_count()
        if self.is_active:
            lines.append("")
            if running:
                lines.append(
                    f"{running} subagent(s) still running. "
                    "They will auto-complete the stage when dispatch finishes."
                )
            elif not self.pending:
                lines.append(
                    "No subagents registered for this stage yet. "
                    "Use `spawn` to dispatch work for this stage."
                )
            else:
                lines.append("All subagents finished. Stage will auto-advance after spawn batch.")

        return "\n".join(lines)

    def _format_plan_started(self) -> str:
        lines = [
            f"Task plan created ({len(self.stages)} stage(s)).",
            "",
            "Workflow:",
            "1. For the active stage, dispatch work with `spawn` (task profiles encouraged).",
            "2. When spawn batch finishes, the runtime auto-waits for subagents and injects stage results.",
            "3. Repeat until all stages complete.",
            "",
            self.format_context_block() or "",
        ]
        return "\n".join(lines).strip()
