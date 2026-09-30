"""Local multi-process run dispatcher backed by SQLite's fenced leases."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable, Sequence

from retrace.engine import Engine, RunResult
from retrace.store import Store
from retrace.workflow import Workflow


class Worker:
    """Poll workflow definitions and execute up to ``max_runs`` owned runs.

    Every process must open its own Store against the same local SQLite file.
    Runs are claimed transactionally; a dead process's expired leases are
    eligible for the next poll. Task side effects remain at-least-once.
    """

    def __init__(
        self,
        store: Store,
        workflow: Workflow | Sequence[Workflow],
        *,
        max_runs: int = 1,
        concurrency: int = 4,
        lease_ttl: float = 15.0,
        poll_interval: float = 1.0,
    ):
        if type(max_runs) is not int or max_runs < 1:
            raise ValueError("max_runs must be a positive integer")
        if not math.isfinite(poll_interval) or poll_interval <= 0:
            raise ValueError("poll_interval must be finite and positive")
        workflows = (workflow,) if isinstance(workflow, Workflow) else tuple(workflow)
        if not workflows or any(not isinstance(item, Workflow) for item in workflows):
            raise ValueError("at least one Workflow is required")
        if len({item.fingerprint for item in workflows}) != len(workflows):
            raise ValueError("workflow definitions must be unique")
        self.store = store
        self.workflow = workflows[0]
        self.workflows = workflows
        self.max_runs = max_runs
        self.poll_interval = poll_interval
        self.engine = Engine(store, concurrency=concurrency, lease_ttl=lease_ttl)

    async def serve(
        self,
        *,
        once: bool = False,
        on_result: Callable[[RunResult], None] | None = None,
        stop_event: asyncio.Event | None = None,
        drain_timeout: float = 30.0,
    ) -> list[RunResult]:
        """Process eligible runs, or keep polling until cancelled.

        ``once`` drains the currently available queue and waits for its claims
        to finish. ``on_result`` is called after each run completes. Setting
        ``stop_event`` stops new claims and gives owned runs ``drain_timeout``
        seconds to finish before their coroutines are cancelled and paused.
        """
        if (
            isinstance(drain_timeout, bool)
            or not isinstance(drain_timeout, (int, float))
            or not math.isfinite(drain_timeout)
            or drain_timeout < 0
        ):
            raise ValueError("drain_timeout must be finite and nonnegative")
        active: dict[str, asyncio.Task[RunResult]] = {}
        results: list[RunResult] = []
        stop_waiter = asyncio.create_task(stop_event.wait()) if stop_event else None
        drain_deadline: float | None = None
        loop = asyncio.get_running_loop()
        try:
            while True:
                if stop_event is not None and stop_event.is_set() and drain_deadline is None:
                    drain_deadline = loop.time() + drain_timeout
                while len(active) < self.max_runs and drain_deadline is None:
                    if stop_event is not None and stop_event.is_set():
                        break
                    claimed = self.store.claim_next_any(
                        self.workflows, self.engine.lease_ttl, exclude=tuple(active)
                    )
                    if claimed is None:
                        break
                    workflow, lease = claimed
                    active[lease.run_id] = asyncio.create_task(
                        self.engine._run_claimed(workflow, lease)
                    )
                if not active:
                    if once or drain_deadline is not None:
                        return results
                    if stop_waiter is None:
                        await asyncio.sleep(self.poll_interval)
                    else:
                        await asyncio.wait((stop_waiter,), timeout=self.poll_interval)
                    continue
                timeout = self.poll_interval
                if drain_deadline is not None:
                    timeout = min(timeout, max(0, drain_deadline - loop.time()))
                waiters: set[asyncio.Task[RunResult] | asyncio.Task[bool]] = set(active.values())
                if stop_waiter is not None and drain_deadline is None:
                    waiters.add(stop_waiter)
                done, _ = await asyncio.wait(
                    waiters, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
                )
                for run_id, task in list(active.items()):
                    if task in done:
                        del active[run_id]
                        result = await task
                        if on_result is not None:
                            on_result(result)
                        if once:
                            results.append(result)
                if drain_deadline is not None and loop.time() >= drain_deadline:
                    return results
        finally:
            if stop_waiter is not None:
                stop_waiter.cancel()
            for task in active.values():
                task.cancel()
            await asyncio.gather(
                *active.values(),
                *((stop_waiter,) if stop_waiter is not None else ()),
                return_exceptions=True,
            )
