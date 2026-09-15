"""Populate disposable real runs for the browser suite; no production database is touched."""

import asyncio
import tempfile
from pathlib import Path

from retrace import Engine, RetryPolicy, Store, Task, Workflow
from retrace.demo import workflow
from retrace.server import serve


async def untrusted_output(ctx):
    return {"html": '<img src=x onerror="window.pwned=true">'}


async def fail(ctx):
    raise RuntimeError("fixture failure")


async def recover(ctx):
    if ctx.attempt == 1:
        raise ConnectionError("fixture recovered after manual retry")
    return {"recovered": True}


async def populate(path):
    with Store(path) as store:
        await Engine(store).run(Workflow("html-output", (Task("output", untrusted_output),)))
        await Engine(store).run(Workflow("failed-run", (Task("fail", fail, retry=RetryPolicy(1)),)))
        recovered = Workflow("recovered-run", (Task("recover", recover, retry=RetryPolicy(1)),))
        result = await Engine(store).run(recovered)
        await Engine(store).retry(recovered, result.run_id)
        await Engine(store).run(workflow, {"crash": False})


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as directory:
        path = str(Path(directory, "browser.db"))
        asyncio.run(populate(path))
        serve(path, 7762)
