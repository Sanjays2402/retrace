"""A deterministic document-indexing simulation, including an optional hard crash."""

import asyncio
import os

from retrace import Context, RetryPolicy, Task, Workflow


async def ingest(ctx: Context):
    await asyncio.sleep(0.35)
    return {"documents": 128, "source": "knowledge-base", "batch": "KB-2026-09"}


async def validate(ctx: Context):
    await asyncio.sleep(0.3)
    return {"valid": ctx.dependencies["ingest"]["documents"], "rejected": 0}


async def chunk(ctx: Context):
    await asyncio.sleep(0.45)
    return {"chunks": ctx.dependencies["validate"]["valid"] * 4, "strategy": "semantic"}


async def enrich(ctx: Context):
    await asyncio.sleep(0.7)
    return {"tagged": 128, "languages": ["en", "fr", "de"]}


async def embed(ctx: Context):
    await asyncio.sleep(0.45)
    if ctx.input.get("crash") and ctx.attempt == 1:
        # Deliberately bypass cleanup to exercise real lease-based recovery.
        os._exit(86)
    if ctx.attempt == 1:
        raise ConnectionError("simulated embedding service rate limit")
    return {"vectors": ctx.dependencies["chunk"]["chunks"], "dimensions": 384}


async def index(ctx: Context):
    await asyncio.sleep(0.5)
    return {"indexed": ctx.dependencies["embed"]["vectors"], "idempotency_key": ctx.idempotency_key}


async def audit(ctx: Context):
    await asyncio.sleep(0.3)
    return {"checks": 12, "passed": 12, "source_documents": ctx.dependencies["validate"]["valid"]}


async def report(ctx: Context):
    await asyncio.sleep(0.25)
    return {"status": "ready", "vectors": ctx.dependencies["index"]["indexed"], "audit": "passed"}


workflow = Workflow(
    "document-indexing",
    (
        Task("ingest", ingest),
        Task("validate", validate, needs=("ingest",)),
        Task("chunk", chunk, needs=("validate",)),
        Task("enrich", enrich, needs=("validate",)),
        Task("embed", embed, needs=("chunk",), retry=RetryPolicy(3, 0.8, 5)),
        Task("index", index, needs=("embed", "enrich")),
        Task("audit", audit, needs=("validate",)),
        Task("report", report, needs=("index", "audit")),
    ),
    version="1",
)
