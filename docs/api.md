# API and command guide

## Define tasks

```python
from retrace import Context, RetryPolicy, Task, Workflow


async def fetch(ctx: Context):
    # Use an async API client here. For external mutations, pass ctx.idempotency_key.
    return {"records": ctx.input["count"]}


workflow = Workflow(
    "ingestion",
    (
        Task(
            "fetch",
            fetch,
            timeout=20,
            retry=RetryPolicy(
                max_attempts=4,
                initial_delay=0.5,
                max_delay=8,
            ),
        ),
    ),
    version="1",
)
```

Names start with an ASCII letter and may contain letters, digits, `_`, and `-`, up to 64
characters. Task functions must be `async def`. A dependency must name another task in the
same DAG. Duplicate names, duplicate dependencies, missing dependencies, and cycles are rejected.

`Context` contains:

| Field | Meaning |
| --- | --- |
| `run_id` | Stable run identifier |
| `task_name` | Current task name |
| `attempt` | 1-based attempt number, including previous interruptions |
| `input` | Independently decoded workflow input |
| `dependencies` | Mapping of direct dependency names to committed JSON outputs |
| `idempotency_key` | Stable SHA-256 key for this logical task within this run |

Default retry policy: three failed attempts, initial delay 0.25 s, max delay 30 s. Default
per-attempt timeout: 60 s. Ordinary `Exception` subclasses, including JSON serialization
failures and timeouts, are retryable under the configured budget. Cancellation is propagated,
not treated as a failure. Return JSON-compatible, small values. Task exceptions are persisted
as a type/message string capped at 4,000 characters; tracebacks are not persisted.

## Run and resume

```python
with Store("jobs.db") as store:
    engine = Engine(store, concurrency=4, lease_ttl=15)
    run_id = store.create(workflow, {"count": 128})
    # Save the ID before execution so the caller can recover after a hard crash.
    result = await engine.resume(workflow, run_id)
```

`Engine.run(workflow, input)` creates and executes a new run in one call. `Store.create` plus
`Engine.resume` lets a caller save the run ID first. Both execution methods return `RunResult`
with `run_id`, `status`, `outputs` of succeeded tasks, and `errors` of failed tasks. A terminal
failed workflow returns a result with `status="failed"`; infrastructure/ownership errors raise.

Important exceptions:

- `RunBusy`: another worker's lease is still live. Retry `resume` after that lease expires or the worker exits.
- `LeaseLost`: this worker no longer owns the run; its writes have been rejected.
- `DefinitionMismatch`: restore the original code/definition or create a new run.
- `KeyError`: requested run does not exist.
- `sqlite3.Error`: storage failure; completed transactions remain the recovery boundary.

Use `Store` as a context manager. It is a single-thread connection; create separate stores in
other threads or processes. Configure a finite positive integer concurrency and lease TTL of
at least 0.3 s. The minimum is intended for tests; the default gives real workloads more margin.
Never call blocking code on the event loop. `asyncio.to_thread` can offload blocking work, but
cancelling the awaiting coroutine does not forcibly stop the underlying thread or its effects.

## Explicit retry and preview

```python
plan = store.retry_plan(workflow, run_id, task_names=["fetch"])
result = await engine.retry(workflow, run_id, tasks=["fetch"])
```

Omit the selection to retry all failed steps. `RetryPlan` contains sorted tuples: `selected`,
`reset`, `preserved` (successful checkpoints), `remaining_failed`, and `remaining_blocked`, plus
`run_id`. Preview does not mutate the database and works with `Store(path, readonly=True)`.

`Store.retry_failed(workflow, run_id, task_names=None)` performs the atomic reset without starting
a worker and returns the applied plan. Follow it with `Engine.resume`. `Engine.retry` combines
those two operations. Retry is valid only for failed runs and currently failed selected steps;
empty, duplicate, unknown, blocked, or successful selections are rejected with `ValueError`.
Live ownership raises `RunBusy`; changed definitions raise `DefinitionMismatch`.

Reset tasks receive a fresh failure budget. `tasks[name]["failures"]` is the current budget count;
`attempts` and the attempt journal remain cumulative. Shared descendants are reopened only when
all dependencies are already successful or included in the recovery plan. The run can remain
failed if an unselected branch is still failed. No inputs, function versions, or successful
outputs are changed. [Read the full recovery contract](recovery.md).

## CLI

Global flags go **before** the subcommand:

```bash
retrace --db jobs.db --concurrency 8 --lease-ttl 30 run my_pipeline:workflow --input '{"count":128}'
retrace --db jobs.db resume my_pipeline:workflow <RUN_ID>
retrace --db jobs.db retry my_pipeline:workflow <RUN_ID> --task fetch --dry-run
retrace --db jobs.db retry my_pipeline:workflow <RUN_ID> --task fetch
retrace --db jobs.db runs
retrace --db jobs.db inspect <RUN_ID>
retrace --db jobs.db events <RUN_ID> --after 42
retrace --db jobs.db serve --port 7760
```

Workflow imports are trusted Python and execute module-level code. The current working directory
is added temporarily to the import path so project-local definitions are importable.

`runs`, `inspect`, `events`, and `retry --dry-run` open read-only connections and never create
a missing database. Run IDs and progress guidance go to stderr; structured results go to stdout. `events` emits JSONL
in ascending event-ID order, fetching every page. The `--after` cursor is exclusive; IDs are
monotonic across the database and may have gaps within a run.

Exit codes: `0` success, `1` terminal workflow failure, `2` invalid input/definition or an
infrastructure error, `130` graceful keyboard interruption. `demo --crash` intentionally exits
with `86`. Ctrl-C pauses active work; a hard kill leaves a lease that must expire before resume.

## Inspector API

The local server starts only if the database exists. It serves:

- `GET /api/runs`: latest 100 run summaries.
- `GET /api/runs/{id}`: run manifest/input, task checkpoints, and attempt history.
- `GET /api/runs/{id}/events?after={cursor}`: up to 500 events and next cursor.

All responses are JSON with `Cache-Control: no-store`. Read-only requests open their own database
connection. Unknown resources return `404`, invalid cursor values `400`, forbidden Host/Origin
headers `403`, and database access errors `503`. There are no write routes, authentication tokens,
or remote bind options. See [SECURITY.md](../SECURITY.md) before using real data.

## Journal filtering in the inspector

Use the task and event-kind selectors together with payload search to narrow the retained
journal. “Show this step's events” applies the selected graph node as a task filter. Clear filters
to restore all retained events. Expand Payload to inspect retry/reset metadata.

“Export shown · JSONL” downloads exactly the matching retained events in ascending event-ID
order. The inspector retains only the latest 1,000 events received; its filters do not change
fetch cursors or discard nonmatching incoming events. For the complete journal, use
`retrace events RUN_ID > events.jsonl`. Export is disabled when nothing matches.
