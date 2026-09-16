# Changelog

## Unreleased

- Read-only Chrome Trace JSON export with task lanes, retry history, and explicit unfinished attempts.
- Inspector graph zoom, responsive fit, and reset controls preserved across live polling.


## 0.2.0 — 2026-09-15

- Selectively retry failed branches through `Engine.retry` and `retrace retry`, including dry-run plans.
- Preserve successful checkpoints, cumulative attempt history, and idempotency keys during retry.
- Atomically reopen only eligible blocked descendants; retain blocks caused by unselected failures.
- Open inspection and dry-run CLI commands read-only, without creating missing databases.

- Filter inspector events by task, kind, and payload text; export shown events as chronological JSONL.
- Expand event payloads while preserving their open state during polling.
- Show lifetime failures separately from the current retry budget.
- Add 11 Python tests and five browser tests, bringing the suites to 46 and 10 respectively.

## 0.1.0 — 2026-09-13

Initial alpha release.

- Versioned async workflow DAGs, dependency validation, and bounded concurrency.
- SQLite checkpoints with atomic event writes, fenced run ownership, and heartbeats.
- Crash recovery, cooperative cancellation, durable capped exponential retries, and timeouts.
- JSON input/output boundaries and stable per-task idempotency keys.
- CLI for execution, resumption, inspection, and complete JSONL event export.
- Read-only local inspector with graph, attempt timeline, checkpoint details, and event journal.
- Deterministic document-indexing demo with retry and hard-crash modes.
- Real process-kill recovery tests, generated DAG reference tests, CI matrix, and wheel smoke test.

Known scope: alpha API; local SQLite only; no automatic worker daemon, remote dashboard,
selective failed-task retry, storage migrations, or exactly-once external effects.
