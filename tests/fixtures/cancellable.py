"""Importable workflow for cross-process cancellation tests."""

import asyncio
from pathlib import Path

from retrace import Task, Workflow


async def checkpoint(ctx):
    return 42


async def slow(ctx):
    directory = Path(ctx.input["directory"])
    (directory / "started").write_text("started")
    try:
        await asyncio.sleep(30)
    finally:
        (directory / "cancelled").write_text("cancelled")


workflow = Workflow(
    "cancellable",
    (Task("checkpoint", checkpoint), Task("slow", slow, needs=("checkpoint",))),
)
