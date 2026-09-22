# Your first recovered workflow

For Python developers with small local data pipelines. You need Python 3.11 or newer
and a local disk. No containers, cloud accounts, or API keys are required.

## 1. Install

```sh
python -m venv .venv
```

Activate with `source .venv/bin/activate` on macOS/Linux, or
`.venv\Scripts\Activate.ps1` in Windows PowerShell. Then:

```sh
python -m pip install git+https://github.com/Sanjays2402/retrace.git
retrace --version
```

This installs from GitHub. PyPI publication is pending; `pip install retrace-engine`
is not the documented installation path yet. Releases also provide wheel downloads.

## 2. Run, crash, recover

```sh
retrace --db first-run.db --lease-ttl 1.5 demo --crash
```

Exit code **86 is expected**: the demo deliberately stops during embedding.
Copy the printed run ID. Wait two seconds for its lease to expire, then replace RUN_ID:

```sh
retrace --db first-run.db resume retrace.demo:workflow RUN_ID
retrace --db first-run.db inspect RUN_ID
retrace --db first-run.db serve
```

Open http://127.0.0.1:7760. The run should be succeeded, the report should contain
512 simulated vectors, and the attempt timeline should retain interrupted work.
Select `ingest`: it should still have one attempt. Completed tasks were reused.

This demo simulates document indexing. To see an actual file/API integration, continue
with [the examples](examples.md).

## 3. Export evidence

```sh
retrace --db first-run.db events RUN_ID > events.jsonl
retrace --db first-run.db trace RUN_ID > trace.json
```

The inspector also offers trace downloads and permalinks to individual steps.
Keep the database: deleting it deletes the checkpoints.

## If you get stuck

- `retrace` not found: activate the virtual environment, or use `python -m retrace`.
- Run busy: wait for the previous worker's lease; do not delete the database to recover.
- Definition mismatch: resume with the original workflow version. Changed implementations
  should use a new version and run.
- Port busy: use `retrace --db first-run.db serve --port 7761`.
- Failed task: fix its external cause, then use `retry`; `resume` does not reset failures.

[Report a problem](https://github.com/Sanjays2402/retrace/issues/new/choose) with your Python
version, command, expected behavior, and a small workflow. Remove secrets from logs.
