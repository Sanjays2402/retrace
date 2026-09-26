"""Local multi-process run dispatcher backed by SQLite's fenced leases."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable

from retrace.engine import Engine, RunResult
from retrace.store import Store
from retrace.workflow import Workflow


class Worker:
    """Poll one workflow definition and execute up to ``max_runs`` owned runs.

    Every process must open its own Store against the same local SQLite file.
    Runs are claimed transactionally; a dead process's expired leases are
    eligible for the next poll. Task side effects remain at-least-once.
    """

    def __init__(
        self,
        store: Store,
        workflow: Workflow,
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
        self.store = store
        self.workflow = workflow
        self.max_runs = max_runs
        self.poll_interval = poll_interval
        self.engine = Engine(store, concurrency=concurrency, lease_ttl=lease_ttl)

    async def serve(
        self, *, once: bool = False, on_result: Callable[[RunResult], None] | None = None
    ) -> list[RunResult]:
        """Process eligible runs, or keep polling until cancelled.

        ``once`` drains the currently available queue and waits for its claims
        to finish. ``on_result`` is called after each run completes.
        """
        active: dict[str, asyncio.Task[RunResult]] = {}
        results: list[RunResult] = []
        try:
            while True:
                while len(active) < self.max_runs:
                    lease = self.store.claim_next(
                        self.workflow, self.engine.lease_ttl, exclude=tuple(active)
                    )
                    if lease is None:
                        break
                    active[lease.run_id] = asyncio.create_task(
                        self.engine._run_claimed(self.workflow, lease)
                    )
                if not active:
                    if once:
                        return results
                    await asyncio.sleep(self.poll_interval)
                    continue
                done, _ = await asyncio.wait(
                    active.values(), timeout=self.poll_interval, return_when=asyncio.FIRST_COMPLETED
                )
                for run_id, task in list(active.items()):
                    if task in done:
                        del active[run_id]
                        result = await task
                        if on_result is not None:
                            on_result(result)
                        if once:
                            results.append(result)
        finally:
            for task in active.values():
                task.cancel()
            await asyncio.gather(*active.values(), return_exceptions=True)
