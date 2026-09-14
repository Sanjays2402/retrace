"""Used by the process-kill integration test, never by production code."""

import asyncio
from pathlib import Path

from retrace import Task, Workflow


async def checkpoint(ctx):
    with Path(ctx.input["directory"], "checkpoint-calls").open("a") as handle:
        handle.write("called\n")
    return 41


async def finish(ctx):
    Path(ctx.input["directory"], "started").write_text(str(ctx.attempt))
    if ctx.attempt == 1:
        await asyncio.sleep(60)
    return ctx.dependencies["checkpoint"] + 1


workflow = Workflow(
    "crash-test",
    (
        Task("checkpoint", checkpoint),
        Task("finish", finish, needs=("checkpoint",)),
    ),
)
