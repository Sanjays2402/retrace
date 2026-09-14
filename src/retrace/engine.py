"""Bounded async DAG scheduling over durable checkpoints."""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from retrace.store import Lease, LeaseLost, Store
from retrace.workflow import Context, Task, Workflow, encode


@dataclass(frozen=True)
class RunResult:
    run_id: str
    status: str
    outputs: dict[str, Any]
    errors: dict[str, str]


class Engine:
    def __init__(self, store: Store, *, concurrency: int = 4, lease_ttl: float = 15.0):
        if type(concurrency) is not int or concurrency < 1:
            raise ValueError("concurrency must be a positive integer")
        if not math.isfinite(lease_ttl) or lease_ttl < 0.3:
            raise ValueError("lease_ttl must be finite and at least 0.3 seconds")
        self.store = store
        self.concurrency = concurrency
        self.lease_ttl = lease_ttl

    async def run(self, workflow: Workflow, input: Any = None) -> RunResult:
        run_id = self.store.create(workflow, input)
        return await self.resume(workflow, run_id)

    async def retry(
        self, workflow: Workflow, run_id: str, *, tasks: Sequence[str] | None = None
    ) -> RunResult:
        """Retry selected failures (all by default) and their eligible blocked descendants."""
        self.store.retry_failed(workflow, run_id, tasks)
        return await self.resume(workflow, run_id)

    def _result(self, run_id: str) -> RunResult:
        tasks = self.store.tasks(run_id)
        return RunResult(
            run_id,
            self.store.run(run_id)["status"],
            {name: task["output"] for name, task in tasks.items() if task["status"] == "succeeded"},
            {name: task["error"] for name, task in tasks.items() if task["status"] == "failed"},
        )

    async def resume(self, workflow: Workflow, run_id: str) -> RunResult:
        lease = self.store.claim(run_id, workflow, self.lease_ttl)
        if lease is None:
            return self._result(run_id)
        scheduler = asyncio.create_task(self._schedule(workflow, lease))
        heartbeat = asyncio.create_task(self._heartbeat(lease))
        try:
            done, _ = await asyncio.wait(
                (scheduler, heartbeat), return_when=asyncio.FIRST_COMPLETED
            )
            if heartbeat in done:
                await heartbeat  # Propagate ownership or storage failures; never keep executing.
            status = await scheduler
            self.store.release(lease, status)
            return self._result(run_id)
        finally:
            scheduler.cancel()
            heartbeat.cancel()
            await asyncio.gather(scheduler, heartbeat, return_exceptions=True)
            with contextlib.suppress(LeaseLost):
                # A successful release has already cleared the owner and is fenced out here.
                self.store.release(lease, "paused")

    async def _heartbeat(self, lease: Lease) -> None:
        while True:
            await asyncio.sleep(self.lease_ttl / 3)
            self.store.heartbeat(lease, self.lease_ttl)

    async def _execute(self, task: Task, lease: Lease, state: dict[str, Any]) -> None:
        attempt = self.store.start_task(lease, task.name)
        snapshots = self.store.tasks(lease.run_id)
        context = Context(
            lease.run_id,
            task.name,
            attempt,
            self.store.run(lease.run_id)["input"],
            {name: snapshots[name]["output"] for name in task.needs},
        )
        try:
            output = await asyncio.wait_for(task.fn(context), timeout=task.timeout)
            # Normalize values through JSON before checkpointing; resumed and fresh values agree.
            output = json.loads(encode(output))
        except Exception as exc:
            failures = state["failures"] + 1
            retry_at = (
                time.time() + task.retry.delay(failures)
                if failures < task.retry.max_attempts
                else None
            )
            error = f"{type(exc).__name__}: {exc}"[:4000]
            self.store.finish_task(lease, task.name, error=error, retry_at=retry_at)
        else:
            self.store.finish_task(lease, task.name, output=output)

    async def _schedule(self, workflow: Workflow, lease: Lease) -> str:
        active: dict[str, asyncio.Task[None]] = {}
        try:
            while True:
                # Retrieve every completed task, including exceptions, before scheduling more work.
                for name in list(active):
                    if active[name].done():
                        await active.pop(name)
                states = self.store.tasks(lease.run_id)
                for task in workflow.tasks:
                    state = states[task.name]
                    if task.name in active or state["status"] not in ("pending", "retrying"):
                        continue
                    dependencies = [states[name]["status"] for name in task.needs]
                    if any(status in ("failed", "blocked") for status in dependencies):
                        self.store.block_task(lease, task.name)
                        states[task.name]["status"] = "blocked"
                    elif (
                        len(active) < self.concurrency
                        and state["next_at"] <= time.time()
                        and all(status == "succeeded" for status in dependencies)
                    ):
                        active[task.name] = asyncio.create_task(self._execute(task, lease, state))
                states = self.store.tasks(lease.run_id)
                if not active and all(
                    t["status"] in ("succeeded", "failed", "blocked") for t in states.values()
                ):
                    return (
                        "failed"
                        if any(t["status"] == "failed" for t in states.values())
                        else "succeeded"
                    )
                if active:
                    await asyncio.wait(
                        active.values(), timeout=0.05, return_when=asyncio.FIRST_COMPLETED
                    )
                else:
                    await asyncio.sleep(0.05)
        finally:
            for future in active.values():
                future.cancel()
            await asyncio.gather(*active.values(), return_exceptions=True)
