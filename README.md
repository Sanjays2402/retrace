# Retrace

**Crash-resumable Python workflows. One SQLite file. No services.**

Retrace runs async dependency graphs, checkpoints completed steps, and resumes interrupted
runs. Bounded concurrency, durable retries, fenced worker leases, and a transactional event
journal are built in. Python 3.11+; zero runtime dependencies. Early alpha.

```python
import asyncio
from retrace import Context, Engine, Store, Task, Workflow

async def extract(ctx: Context):
    return {"records": 42}

async def summarize(ctx: Context):
    return {"processed": ctx.dependencies["extract"]["records"]}

workflow = Workflow("pipeline", (
    Task("extract", extract),
    Task("summarize", summarize, needs=("extract",)),
))

async def main():
    with Store("retrace.db") as store:
        result = await Engine(store).run(workflow)
        print(result)

asyncio.run(main())
```

Completed checkpoints are reused. External side effects are **at least once**: pass
`ctx.idempotency_key` to an API that supports deduplication. This is an embedded engine
for trusted async Python code, not a distributed scheduler or a sandbox.

MIT licensed. CLI, visual inspector, architecture guide, and failure-injection tests are
being added in the initial release.
