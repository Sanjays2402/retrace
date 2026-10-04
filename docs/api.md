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
| `signal` | JSON payload of the declared signal gate, or `None` for ordinary tasks |
| `idempotency_key` | Stable SHA-256 key for this logical task within this run |

Default retry policy: three failed attempts, initial delay 0.25 s, max delay 30 s. Default
per-attempt timeout: 60 s. Ordinary `Exception` subclasses, including JSON serialization
failures and timeouts, are retryable under the configured budget. Cancellation is propagated,
not treated as a failure. Return JSON-compatible, small values. Task exceptions are persisted
as a type/message string capped at 4,000 characters; tracebacks are not persisted.

### Classify failures and spread retries

```python
policy = RetryPolicy(
    max_attempts=5,
    initial_delay=0.5,
    max_delay=15,
    jitter=True,
    non_retryable=(ValueError, PermissionError),
)
```

`non_retryable` is a tuple of exception **classes**, matched with `isinstance`, including
subclasses. A matching error records one failed attempt and immediately fails the task;
its descendants are blocked while independent branches can finish. Other `Exception`
subclasses keep the configured failure budget. `TimeoutError` can also be excluded.
Cancellation continues to propagate without consuming that budget.

With `jitter=True`, each retry delay is sampled uniformly from zero up to the capped
exponential delay. Retrace samples only when scheduling a retry, then commits the absolute
deadline with the failed attempt and journal event. Reopening or resuming the run reuses
that deadline. The default `jitter=False` retains deterministic backoff.

Both options belong to the workflow fingerprint. Existing definitions with the defaults
retain their original fingerprint; enabling either option changes it. Keep the original
policy available to resume existing runs. Explicit manual retry can reopen a permanent
failure after its external cause has been corrected; it uses the same classification on
subsequent attempts.

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

## Durable signals

Set `Task(..., wait_for="decision")` to gate a task on an external event. After its
dependencies succeed, Retrace records the task as `waiting` and releases the run lease.
No attempt or timeout budget is spent while it waits. The run result has `status="waiting"`.
The task's `ctx.signal` receives the decoded JSON payload when it eventually executes.

```bash
retrace --db jobs.db run examples.approval:workflow --input '{"request":"release-42"}'
# Copy the run ID printed on stderr; the run now waits without occupying a worker.
retrace --db jobs.db signal RUN_ID decision --payload '{"approved":true}'
retrace --db jobs.db resume examples.approval:workflow RUN_ID
```

Or call `store.signal(run_id, "decision", {"approved": True})`. The signal can arrive
before the gate is reached. Delivery is one-shot per run and signal name: identical
redelivery returns `False`, while a different payload raises `ValueError`. The payload is
immutable and survives worker crashes and task retries. Multiple tasks may use the same
signal, each receiving its own decoded copy. `Store.signals(run_id)` lists delivered
signals. Signals must be declared by at least one task and cannot be newly delivered to
a terminal run. A waiting run is not claimed again until a matching signal arrives;
`Worker.serve()` automatically resumes it on the next poll. Waiting has no deadline yet;
cancel the run if the external decision will never arrive. Cancellation also fences an
active worker and leaves the signal history inspectable.

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

## Local worker pool

`Store.create` can enqueue a run without immediately executing it. A `Worker` polls the store
for pending, paused, or expired runs with the same workflow fingerprint:

```python
from retrace import Store, Worker

with Store("jobs.db") as store:
    run_id = store.create(workflow, {"count": 128})
    # In a worker process using its own Store connection:
    results = await Worker(store, workflow, max_runs=2).serve(once=True)
```

`Worker.serve(once=True)` returns completed `RunResult` values and exits when no eligible
run remains. `serve(on_result=callback)` polls until cancelled and reports each completed run
to the synchronous callback. `max_runs` bounds parallel runs in a process; `concurrency`
bounds tasks in each run. `poll_interval` defaults to one second. Workers on the same machine
must use separate `Store` connections to the same local SQLite file. `Store.claim_next` makes
the queue selection and fenced lease assignment one transaction. A crash can still repeat an
external side effect before its checkpoint; use `Context.idempotency_key` downstream.

Use `Worker(store, (workflow_a, workflow_b), max_runs=4)` for a shared pool. Each claim picks
the least recently served eligible definition, then its oldest run. This round-robin state is
persisted in SQLite and shared by competing local processes. A single-definition worker keeps
its original FIFO behavior. Configure limits with
`store.configure_queue(workflow, max_active=2, max_queued=100)` or the CLI `queue --configure`
command. `max_active` caps live leases across workers; `max_queued` bounds pending and paused
runs at submission time. `Store.create` raises `QueueFull` when full, except that an identical
submission key returns its existing ID. `Store.queue_stats()` reports counts and
`oldest_ready_age_seconds`, or `None` when no run is eligible. A direct `Engine.resume` can
still claim past the worker cap for manual recovery.

Pass `stop_event=asyncio.Event()` and `drain_timeout=30` to `Worker.serve` to request a
graceful stop. Once the event is set, the worker claims no more runs. It waits up to the
timeout for owned runs to finish, then cancels remaining coroutines, marks their attempts
interrupted, and releases those runs as `paused` for immediate takeover. `drain_timeout=0`
pauses immediately; negative or non-finite values are rejected. Completed runs still reach
`on_result` (or the `once=True` result list). Unfinished runs remain inspectable and resumable.
The CLI worker handles SIGTERM this way by default; use `--drain-timeout SECONDS` to tune it.
Ctrl-C retains its immediate interruption behavior. A hard kill cannot drain and leaves
recovery to the lease timeout. Blocking or cancellation-suppressing tasks can delay shutdown.

`Store.create(workflow, input, key="order-42", ready_at=timestamp)` supports retries by
producers and delayed dispatch. A key is unique across the database, limited to 128 characters,
and stored as a SHA-256 hash. Repeating it with the same workflow fingerprint and canonical JSON
input returns the original run ID without creating new tasks or events; changing either raises
`ValueError`. The first submission's `ready_at` wins. Workers ignore pending runs before that
Unix timestamp. Explicit `Engine.resume` may claim one early, and claiming consumes the delay.
This is **submission** deduplication; it does not make task side effects exactly once.

## Durable run cancellation

`store.cancel(run_id)` atomically marks a pending, running, or paused run `cancelled` and
returns `True`. Calling it again returns `False`. An unknown ID raises `KeyError`; succeeded
or failed runs raise `ValueError`. The CLI equivalent is `retrace --db jobs.db cancel <RUN_ID>`.

Cancellation advances the ownership epoch and clears the lease so the previous worker cannot
commit another checkpoint. Running attempts become `interrupted`; unfinished tasks become
`cancelled`, and completed outputs remain available. A worker observing the lost lease stops
its active coroutines and returns a `RunResult(status="cancelled")`. The event journal records
the cancellation. A cancelled run is terminal: `resume` returns its stored result and `retry`
does not reopen it. Create a new run if the workflow should execute again. External effects
that happened before cancellation cannot be rolled back, and blocking code may continue until
it cooperates. Cancelling an awaiting `Engine` coroutine or pressing Ctrl-C instead pauses the
run for later resumption.

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

## Find runs and page through history

Filter run summaries at the database level to find work that needs attention, including
runs older than the default 100-entry listing:

```bash
retrace --db jobs.db runs --status failed --status waiting
retrace --db jobs.db runs --workflow ingestion --status failed --limit 25
retrace --db jobs.db runs --workflow ingestion --status failed --limit 25 --before <LAST_RUN_ID>
```

Repeated status filters match **any** listed status. The workflow filter matches the exact
workflow name across all versions; combine it with status filters to match both. Valid statuses
are `pending`, `running`, `paused`, `waiting`, `succeeded`, `failed`, and `cancelled`.
An expired worker lease still has persisted status `running` until recovery; this query does
not reclassify it as paused or interrupted.

```python
page = store.runs(limit=25, statuses=["failed", "waiting"], workflow="ingestion")
if page:
    older = store.runs(
        limit=25,
        statuses=["failed", "waiting"],
        workflow="ingestion",
        before=page[-1]["id"],
    )
```

Results sort by creation time descending, then run ID descending to resolve timestamp ties.
Use the last returned run's ID as the exclusive `before` cursor. Newer submissions do not
shift later pages. The cursor run need not match your filters. It must still exist: pruning
it makes the cursor invalid (`KeyError` in Python, exit code 2 in the CLI, HTTP 404).
Run status can change between requests, so pagination is a live view, not a frozen snapshot.
Stop when a page is empty. Limits retain the existing bounds of 1–1000 (default 100).
Queries remain read-only and return the existing JSON array of summaries.

## Measure workflow health

The local inspector's **Workflow health** panel compares workflow definitions over 24 hours,
7 days, or 30 days, updating every 10 seconds. This helps operators find failing workflows,
slow completion, recurring failed attempts, and running jobs whose worker leases expired.
Metrics are computed from the real SQLite database independently of the loaded run list.

```bash
retrace --db jobs.db health
retrace --db jobs.db health --hours 168 > weekly-health.json
```

```python
from retrace.health import workflow_health

with Store("jobs.db", readonly=True) as store:
    metrics = workflow_health(store, since=unix_timestamp, limit=10000)
```

The period is a **creation-time cohort**: it includes runs created at or after `since`,
and uses their current status and all retained attempt history. It does not count every
completion that happened during the period. Each group has its own workflow name, version,
and fingerprint, so different definitions are not silently mixed.

- `failure_rate` is failed runs divided by succeeded plus failed runs. Cancelled, queued,
  paused, waiting, and running runs do not enter the denominator. With no completed runs,
  it is `null`, not an invented 0% success rate.
- `p50_completion_seconds` and `p95_completion_seconds` use nearest-rank percentiles of
  creation-to-final-update elapsed time for succeeded and failed runs. Queue delay, signal
  waits, downtime, and manual retries are included; these are not task execution durations.
- `failed_attempts` and `interrupted_attempts` count retained attempts with those statuses.
  `recovered_runs` counts succeeded runs that have at least one failed attempt.
- `expired_leases` counts currently running runs whose lease expired by `captured_at`.
  It indicates recovery eligibility, not proof a worker process is dead.

The latest 10,000 matching runs are sampled by default. `matched_runs`, `sampled_runs`,
`limit`, and `truncated` make that scope explicit. Python limits must be integers from 1
to 10,000; `since` must be a finite nonnegative Unix timestamp. CLI/API `hours` must be
finite and positive. Retention affects available history: metrics are not a permanent audit
log or an SLA guarantee. Queries take a coherent read-only snapshot, omit inputs, outputs,
and exception messages, and never migrate or create a missing database. Call the Python
function outside an existing transaction.

## Check operational thresholds

Add maximum thresholds to `health` when a script needs a machine-readable decision:

```bash
retrace --db jobs.db health --hours 168 \
  --max-failure-rate 0.05 --max-p95-seconds 60 \
  --max-expired-leases 0 --min-completed 20 > health-check.json
```

Thresholds apply **per workflow definition**, rather than hiding a failing definition in
an aggregate average. Failure-rate thresholds are fractions from 0 to 1; latency thresholds
are finite nonnegative seconds; expired-lease thresholds are nonnegative integers. Boundaries
are inclusive: an observation equal to its maximum passes. Configure any combination.
Without thresholds, `health` retains its metrics-only behavior and output format.

The JSON includes a `checks` object with the overall status, configured thresholds, minimum
sample size, reasons, and each definition's observed values and individual checks.

| Exit code | Meaning with thresholds configured |
| --- | --- |
| `0` | Every configured check passed with adequate data |
| `1` | At least one observed threshold was exceeded |
| `2` | Invalid options or a database/infrastructure error |
| `3` | No known breach, but insufficient data to pass |

Rate and latency checks require at least `--min-completed` succeeded/failed runs per
definition (default 5, positive integer). Waiting, running, and cancelled runs do not count
toward this floor. Expired-lease checks do not require completed runs. Empty cohorts and
truncated run samples cannot pass. A known breach takes precedence over insufficient data;
the reasons and individual check statuses still describe both conditions.

```python
from retrace.health import evaluate_health, workflow_health

with Store("jobs.db", readonly=True) as store:
    metrics = workflow_health(store, since=unix_timestamp)
checks = evaluate_health(
    metrics,
    max_failure_rate=0.05,
    max_p95_seconds=60,
    max_expired_leases=0,
    min_completed=20,
)
```

`evaluate_health` requires at least one threshold, reads a `workflow_health` result without
mutating it, and performs no writes or network calls. Use the status or exit code in your
own monitoring or deployment checks. These are observed cohort checks, not an SLA guarantee;
the creation-time, latency, sampling, and retention semantics above still apply.

## Export a diagnostic report

Use **Download report** in the local inspector to save a JSON snapshot of the selected
run, its definition, checkpoint metadata, complete attempt history, signal names, and recent
journal events. The CLI and Python API offer the same report:

```bash
retrace --db jobs.db report <RUN_ID> > run.report.json
retrace --db jobs.db report <RUN_ID> --event-limit 100 > recent.report.json
# Explicitly include application data for your own investigation:
retrace --db jobs.db report <RUN_ID> --include-payloads > full.report.json
```

```python
from retrace.report import export_report

with Store("jobs.db", readonly=True) as store:
    report = export_report(store, run_id, event_limit=100)
```

Default reports omit workflow input, task outputs, signal payloads, all event payloads,
and task/attempt exception messages. `--include-payloads` includes those values; worker
owner tokens and submission key hashes are always excluded. Run IDs, workflow/task/signal
names, function identifiers, versions, timings, and status remain visible in either mode.
Review these identifiers before sharing a report. The inspector's report route always uses
the default policy; query parameters cannot enable application payloads.

`report_version` identifies the JSON format (currently 1), and `schema_version` identifies
the source database schema. `includes_payloads` records the export policy. `journal.events`
contains the latest `event_limit` events in ascending ID order; `journal.total`, `limit`,
and `truncated` describe the retained database journal and any export truncation. Limits
must be integers from 1 to 1,000; default 1,000. Task and attempt history is complete.
Use `retrace events` for the full journal. Whole-run retention can already have removed
other runs; an unknown or pruned run returns an error.

Export reads all tables in one transaction so concurrent worker updates cannot mix
different run states in the report. It does not import workflow code, migrate the database,
or change checkpoints. Call the Python exporter outside an existing transaction. Reports
are for diagnosis; use a database backup to restore execution.

## Back up and restore a database

```bash
mkdir backups
retrace --db jobs.db backup backups/before-cleanup.db
retrace --db backups/before-cleanup.db runs
```

`backup DESTINATION` opens the source read-only and uses the
[SQLite online backup API](https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.backup).
It includes committed WAL data while other connections may continue writing. The snapshot
contains the schema, inputs, checkpoints, attempts, signals, submission keys, queue policies,
and event sequences. It is a consistent database snapshot; it does not include separately
stored files or external service state.

The destination's parent directory must exist. Existing files, symlinks, and SQLite sidecars
at the destination are rejected; source database and sidecar paths are also rejected. Retrace
copies into a private temporary file, checks SQLite integrity and foreign keys, consolidates
it into a standalone database, then publishes it without overwriting an existing path. Failed
copies are removed. Publication requires a filesystem that supports hard links. On POSIX,
the backup keeps the temporary file's owner-only permissions.

`--timeout 30` sets a finite positive copy timeout in seconds, checked between page batches
and lock retries. It does not limit the subsequent integrity checks or file publication. JSON output contains
`path`, `size_bytes`, `schema_version`, and `runs`, all describing the completed snapshot.
The source schema version is preserved; opening an older backup for writing applies the
normal schema migrations.

```python
from retrace import Store

with Store("jobs.db", readonly=True) as store:
    result = store.backup("backups/before-cleanup.db", timeout=30)
```

`Store.backup(destination, *, timeout=30)` returns an immutable `BackupResult`. It also
supports in-memory stores. Call it outside a transaction on the source connection.

To restore, use the backup as a **separate database path**, inspect it first, and load the
original workflow definition when resuming:

```bash
retrace --db backups/before-cleanup.db inspect RUN_ID
retrace --db backups/before-cleanup.db resume my_pipeline:workflow RUN_ID
```

Stop the original workers before executing restored runs. Live leases in a snapshot must
expire before takeover. Successful checkpoints are reused; external effects made after the
snapshot may execute again. Retain downstream idempotency records and use the existing
`ctx.idempotency_key` to handle that replay window. Stored backups contain the same private
inputs and outputs as the original database.

## Retain useful runs and clean up old history

```bash
# Preview completed unkeyed runs last updated more than 30 days ago.
retrace --db jobs.db prune --older-than 30 --limit 100
# Apply after reviewing the preview. The selection is checked again under a write lock.
retrace --db jobs.db prune --older-than 30 --limit 100 --apply
```

Cleanup defaults to `succeeded` and `cancelled` runs. Add `--include-failed` only when you
no longer need to retry old failures. Pending, paused, waiting, running, and owned runs
are always protected. Runs with submission keys are also always protected, including
their results, so repeating a key continues to return the original run and conflicting
work stays rejected.

`--older-than` is a finite positive age in **days**, measured from `updated_at` with a
strict cutoff. The default batch is 100 runs; `--limit` accepts 1–500. Batches select the
oldest updated runs first, then break ties by run ID. Repeat the command to process more
batches. Each removed run loses its input, manifest, checkpoints, attempts, signals, and
events together in one transaction. Removed runs cannot be inspected, resumed, or retried.
Save a database backup or exported traces first if you need their history.

Both modes print JSON containing `applied`, the cutoff timestamp, `run_ids`, record counts
(`tasks`, `attempts`, `events`, `signals`), and `protected_keyed_runs`. The latter counts all
otherwise age/status-eligible keyed runs, independently of the batch limit. A preview is a
snapshot, not a reservation: another worker or operator can change eligibility before apply.
Save the apply receipt if you need a record of cleanup.

```python
import time
from retrace import Store

cutoff = time.time() - 30 * 86400
with Store("jobs.db", readonly=True) as store:
    preview = store.prune_plan(cutoff, limit=100)
with Store("jobs.db") as store:
    receipt = store.prune(cutoff, limit=100)
```

`Store.prune_plan(before, *, limit=100, include_failed=False)` works on read-only
connections and returns an immutable `PrunePlan`. `Store.prune` uses the same arguments,
reselects in its write transaction, and returns counts for the records it actually removed.
Cleanup requires schema v4; migrate an older database by opening it for writing before
previewing. No new schema migration is required for cleanup.

Retained event IDs are never renumbered, and new events remain monotonic. SQLite reuses
freed pages internally; this operation does not shrink the database file or run `VACUUM`.
Queue policies and the durable scheduling cursor remain intact. Keyed-run expiration and
partial event compaction are outside this cleanup contract.

## CLI

Global flags go **before** the subcommand:

```bash
retrace --db jobs.db --concurrency 8 --lease-ttl 30 run my_pipeline:workflow --input '{"count":128}'
retrace --db jobs.db submit my_pipeline:workflow --input '{"count":128}'
retrace --db jobs.db submit my_pipeline:workflow --input '{"count":128}' --key job-42 --delay 60
retrace --db jobs.db worker my_pipeline:workflow --max-runs 2 --drain-timeout 30
retrace --db jobs.db worker my_pipeline:workflow --once
retrace --db jobs.db cancel <RUN_ID>
retrace --db jobs.db resume my_pipeline:workflow <RUN_ID>
retrace --db jobs.db retry my_pipeline:workflow <RUN_ID> --task fetch --dry-run
retrace --db jobs.db retry my_pipeline:workflow <RUN_ID> --task fetch
retrace --db jobs.db runs
retrace --db jobs.db inspect <RUN_ID>
retrace --db jobs.db events <RUN_ID> --after 42
retrace --db jobs.db backup backups/before-cleanup.db
retrace --db jobs.db prune --older-than 30
retrace --db jobs.db serve --port 7760
```

Workflow imports are trusted Python and execute module-level code. The current working directory
is added temporarily to the import path so project-local definitions are importable.

`metrics`, `health`, `runs`, `inspect`, `events`, `report`, `backup`, `prune` without `--apply`, and `retry --dry-run` open read-only connections and never create
a missing database. Run IDs and progress guidance go to stderr; structured results go to stdout. `events` emits JSONL
in ascending event-ID order, fetching every page. The `--after` cursor is exclusive; IDs are
monotonic across the database and may have gaps within a run.

Exit codes: `0` success, `1` terminal workflow failure (or a configured health threshold breach), `2` invalid input/definition or an
infrastructure error, `130` graceful keyboard interruption. Health checks additionally use `3`
for insufficient data when no breach is known. `demo --crash` intentionally exits with `86`.
Ctrl-C pauses active work; a hard kill leaves a lease that must expire before resume.
`submit` returns a JSON object with the run ID, current status, and eligibility timestamp.
`--delay` is a nonnegative number of seconds; `--key` provides idempotency within the database.
`worker --once` prints a JSON array of
completed runs; a continuous worker emits one JSON object per completed run. Individual task
failures appear in each result and do not stop the worker process.

## Prometheus monitoring

The inspector serves `GET /metrics` in the [Prometheus text exposition format](https://prometheus.io/docs/instrumenting/exposition_formats/)
(version 0.0.4). Export the same snapshot from Python or the CLI without starting a server:

```python
from retrace import Store
from retrace.metrics import prometheus_metrics

with Store("jobs.db", readonly=True) as store:
    text = prometheus_metrics(store)
```

```bash
retrace --db jobs.db metrics > queue.prom
retrace --db jobs.db serve --port 7760
```

A Prometheus instance running on the same host can scrape the loopback inspector:

```yaml
scrape_configs:
  - job_name: retrace
    scrape_interval: 15s
    static_configs:
      - targets: ["127.0.0.1:7760"]
```

| Metric | Labels | Meaning |
| --- | --- | --- |
| `retrace_queue_definitions` | none | Stored definitions or configured queue policies |
| `retrace_queue_runs` | `workflow`, `fingerprint`, `state` | Current ready, delayed, paused, waiting, active, or recoverable run count |
| `retrace_queue_limit` | `workflow`, `fingerprint`, `resource` | Configured `active` or `queued` capacity; unlimited limits have no sample |
| `retrace_queue_oldest_eligible_seconds` | `workflow`, `fingerprint` | Oldest ready, paused, or expired-lease wait; absent if no eligible run |

All metrics are **gauges**, collected from one read transaction over the whole database.
They describe current state, not lifetime execution counters. Terminal runs are excluded from
queue counts. Delayed and signal-waiting runs are excluded from eligible age; queued capacity
counts ready, delayed, and paused runs. A configured policy with no runs emits zero state counts.
The CLI does not create missing databases or import workflow code.

Example alert rules detect recoverable leases and excessive eligible waits:

```yaml
groups:
  - name: retrace-queues
    rules:
      - alert: RetraceExpiredLease
        expr: retrace_queue_runs{state="recoverable"} > 0
        for: 2m
      - alert: RetraceQueueStalled
        expr: retrace_queue_oldest_eligible_seconds > 300
        for: 5m
```

Choose thresholds to match expected workload latency. A long eligible wait can be caused by
active limits, absent workers, or recovery; delayed dispatch and signal waits are different states.
Use Prometheus's `up{job="retrace"}` to detect scrape failures; a missing series is not proof of
an empty queue. An empty database emits `retrace_queue_definitions 0` and metric metadata.

Labels contain workflow names and exact fingerprints, never run IDs, inputs, outputs, exception
messages, or worker owners. Different definitions of the same name remain separate series.
Cardinality grows with stored definitions and policies; review retention and policy creation
when generating definitions dynamically. The endpoint preserves loopback-only binding and
Host/Origin validation. A Prometheus container's own loopback does not reach a host inspector;
use a same-host collector or an explicit, secured forwarding arrangement.

## Inspector API

The inspector starts with the latest 100 runs. **Show 100 more** expands the list by 100
at a time, up to 1,000, while keeping polling active for the entire loaded list. Search,
status filters, and summary counts apply to loaded runs. **Latest 100** reduces the list
without changing the selected run or its checkpoints. The history count announces updates
to screen readers, and controls are disabled while a requested history change loads.
History changes made during live polling are fetched as soon as the active refresh finishes.
At the 1,000-run cap, use `retrace runs --before <LAST_RUN_ID>` to browse older history.
The expanded list remains a live view: new submissions can move the oldest entries out
of the loaded window.

The **Queue overview** panel refreshes every 10 seconds across the entire database,
independent of run-history filters and health lookback. Each row represents an exact workflow
fingerprint and shows ready, delayed, paused, signal-waiting, active, and recoverable runs.
Active and queued capacity display configured limits and a **Full** indicator. Queued capacity
counts pending (including delayed) and paused runs; waiting runs consume neither active nor
queued capacity. Recoverable runs have expired leases and can be reclaimed by workers.
Oldest eligible age includes ready, paused, and expired-lease runs; delayed and signal-waiting
runs are excluded. An unavailable queue snapshot is marked as potentially stale while normal
run inspection remains usable. Empty configured policies are shown even without any runs.

The local server starts only if the database exists. It serves:

- `GET /api/runs`: latest 100 run summaries. Optional `limit`, repeated `status`,
  exact `workflow`, and exclusive `before` run-ID parameters filter and page results,
  e.g. `/api/runs?status=failed&status=waiting&workflow=ingestion&limit=25`.
- `GET /api/runs/{id}`: run manifest/input, task checkpoints, and attempt history.
- `GET /api/runs/{id}/report`: a diagnostic report without application payloads or errors.
- `GET /metrics`: Prometheus text queue gauges (version 0.0.4), instead of JSON.
- `GET /api/queue`: a coherent read-only snapshot of all definition queues and limits.
- `GET /api/health?hours=24`: workflow reliability metrics for a creation-time cohort.
- `GET /api/runs/{id}/events?after={cursor}`: up to 500 events and next cursor.

API responses are JSON; `/metrics` uses Prometheus text. All responses use `Cache-Control: no-store`. Read-only requests open their own database
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
