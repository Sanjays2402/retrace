"""Importable workflow for graceful worker shutdown process tests."""

import asyncio
from pathlib import Path

from retrace import Task, Workflow


async def work(ctx):
    Path(ctx.input["marker"]).write_text("started")
    await asyncio.sleep(ctx.input["delay"])
    return "done"


workflow = Workflow("drainable", (Task("work", work),))
