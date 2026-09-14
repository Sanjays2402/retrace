"""Run with: retrace run examples.pipeline:workflow --input '{"values": [3, 7, 11]}'"""

import asyncio

from retrace import Context, Task, Workflow


async def extract(ctx: Context):
    return ctx.input["values"]


async def total(ctx: Context):
    await asyncio.sleep(0.1)  # Replace with a cooperative async I/O operation.
    return sum(ctx.dependencies["extract"])


async def count(ctx: Context):
    return len(ctx.dependencies["extract"])


async def summarize(ctx: Context):
    count = ctx.dependencies["count"]
    return {"count": count, "mean": ctx.dependencies["total"] / count if count else None}


workflow = Workflow(
    "analytics",
    (
        Task("extract", extract),
        Task("total", total, needs=("extract",)),
        Task("count", count, needs=("extract",)),
        Task("summarize", summarize, needs=("total", "count")),
    ),
)
