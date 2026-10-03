"""Simulate transient contention and permanent invalid input without external services.

retrace run examples.classified_retry:workflow --input '{"count":128}'
retrace run examples.classified_retry:workflow --input '{"count":-1}'
"""

from retrace import RetryPolicy, Task, Workflow


async def fetch(ctx):
    count = ctx.input.get("count")
    if type(count) is not int or count < 1:
        raise ValueError("count must be a positive integer")
    if ctx.attempt == 1:
        raise ConnectionError("simulated service contention")
    return {"records": count}


workflow = Workflow(
    "classified-retry",
    (
        Task(
            "fetch",
            fetch,
            retry=RetryPolicy(
                max_attempts=4,
                initial_delay=0.1,
                max_delay=1,
                jitter=True,
                non_retryable=(ValueError, TypeError),
            ),
        ),
    ),
)
