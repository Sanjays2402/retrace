"""Reproducible local-disk overhead baseline; not a distributed throughput claim."""

import argparse
import asyncio
import json
import platform
import statistics
import tempfile
import time
from pathlib import Path

from retrace import Engine, Store, Task, Workflow


async def noop(ctx):
    return {"task": ctx.task_name}


async def measure(task_count, repetitions):
    workflow = Workflow("benchmark", tuple(Task(f"t{i}", noop) for i in range(task_count)))
    samples = []
    with tempfile.TemporaryDirectory() as directory:
        for repeat in range(repetitions):
            with Store(Path(directory, f"run-{repeat}.db")) as store:
                start = time.perf_counter()
                result = await Engine(store, concurrency=8).run(workflow)
                samples.append(time.perf_counter() - start)
                assert result.status == "succeeded" and len(result.outputs) == task_count
    median = statistics.median(samples)
    return {
        "tasks": task_count,
        "repetitions": repetitions,
        "seconds": samples,
        "median_seconds": median,
        "tasks_per_second": task_count / median,
        "concurrency": 8,
        "storage": "SQLite WAL + synchronous=FULL on temporary local disk",
        "python": platform.python_version(),
        "os": platform.system(),
        "machine": platform.machine(),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=int, default=100)
    parser.add_argument("--repetitions", type=int, default=5)
    args = parser.parse_args()
    if args.tasks < 1 or args.repetitions < 1:
        parser.error("tasks and repetitions must be positive")
    print(json.dumps(asyncio.run(measure(args.tasks, args.repetitions)), indent=2))
