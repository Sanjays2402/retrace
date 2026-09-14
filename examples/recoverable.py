"""Simulate an unavailable service, fix it, and retry without repeating extraction.

retrace run examples.recoverable:workflow --input '{"ready_file":"service.ready"}'
# Create service.ready, then use the printed run ID:
retrace retry examples.recoverable:workflow RUN_ID --dry-run
retrace retry examples.recoverable:workflow RUN_ID
"""

import asyncio
from pathlib import Path

from retrace import RetryPolicy, Task, Workflow


async def extract(ctx):
    return {"records": 128}


async def publish(ctx):
    await asyncio.sleep(0)
    if not Path(ctx.input["ready_file"]).is_file():
        raise ConnectionError("simulated service unavailable; create the ready_file to recover")
    return {"published": ctx.dependencies["extract"]["records"], "key": ctx.idempotency_key}


workflow = Workflow(
    "recoverable",
    (
        Task("extract", extract),
        Task("publish", publish, needs=("extract",), retry=RetryPolicy(2, 0.1, 1)),
    ),
)
