"""Small CSV ingestion with immutable checkpointed input and exact integer totals."""

import asyncio
import csv
from pathlib import Path

from retrace import Task, Workflow


def read_rows(path):
    with Path(path).open(newline="", encoding="utf-8") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames != ["order_id", "amount_cents"]:
            raise ValueError("expected order_id,amount_cents columns")
        rows = []
        for row in reader:
            if len(rows) >= 1000:
                raise ValueError("example supports at most 1000 rows; batch larger inputs")
            rows.append(row)
        return rows


async def extract(ctx):
    return await asyncio.to_thread(read_rows, ctx.input["path"])


async def validate(ctx):
    rows, seen = [], set()
    for row in ctx.dependencies["extract"]:
        order_id = row["order_id"]
        amount = int(row["amount_cents"])
        if not order_id or order_id in seen or amount < 0:
            raise ValueError("order IDs must be unique and amounts nonnegative")
        seen.add(order_id)
        rows.append({"order_id": order_id, "amount_cents": amount})
    return rows


async def summarize(ctx):
    rows = ctx.dependencies["validate"]
    return {"orders": len(rows), "total_cents": sum(row["amount_cents"] for row in rows)}


workflow = Workflow(
    "csv-orders",
    (
        Task("extract", extract),
        Task("validate", validate, needs=("extract",)),
        Task("summarize", summarize, needs=("validate",)),
    ),
)
