# Changelog

## Unreleased

- Add a live inspector queue overview with per-definition backlog, capacity limits,
  expired-lease recovery counts, oldest eligible waits, and a read-only queue API.

- Add configurable operational health checks for failure rate, p95 completion time, and expired leases.
- Return per-definition observed values and decisions, with exit codes for breach and insufficient data.
- Require minimum completion samples for rate/latency checks; prevent empty or truncated cohorts from passing.

- Add workflow health analytics in Python, CLI, inspector API, and a live dashboard panel.
- Report cohort failure rates, p50/p95 completion latency, failed/interrupted attempts,
  recovered runs, and expired leases per workflow definition.
- Bound run sampling, expose truncation, and document creation-time and retention semantics.

- Add diagnostic report downloads to the inspector, a read-only `report` CLI, and Python export API.
- Omit application payloads and exception messages by default, with explicit CLI/Python opt-in.
- Export a coherent snapshot with complete attempt history and a bounded journal with truncation metadata.

- Add live inspector history expansion in 100-run batches up to 1,000 runs, with a reset control.
- Keep search, attention filters, and counts aligned with the loaded history; add browser tests
  for filtering, refresh persistence, reset, and the bounded list.

- Add database-level run history filters for status and exact workflow name in Python, CLI, and inspector API.
- Add exclusive run-ID pagination with deterministic timestamp tie-breaking and stable pages under new submissions.
- Preserve existing listing response formats and read-only access; validate filters and missing cursors.

- Add `Store.backup` and a read-only-source `backup` CLI for standalone SQLite snapshots.
- Include committed WAL state, verify integrity and foreign keys, and publish without overwriting files.
- Add backup copy timeouts, temporary-file cleanup, and checkpoint restore/concurrent-writer tests.

- Add whole-run retention with `Store.prune_plan`, `Store.prune`, and a preview-first `prune` CLI.
- Protect unfinished and keyed runs; delete selected terminal runs and dependent records atomically.
- Preserve event cursors and submission-key deduplication across cleanup batches.

- Add `RetryPolicy.non_retryable` to stop automatic retries for permanent errors and subclasses.
- Add opt-in full jitter, sampled once per retry and persisted across recovery.
- Preserve workflow fingerprints for existing default policies and document policy compatibility.
- Add a runnable classified-retry example and recovery tests for jittered deadlines.

## 0.9.0 — 2026-09-29

- Add durable round-robin scheduling across workflow definitions in a shared local worker pool.
- Enforce per-definition active-run and queued-run limits transactionally across processes.
- Expose queue depth and oldest eligible wait age through Python and CLI, with schema v4 migration.

## 0.8.0 — 2026-09-28

- Add durable one-shot signals and task wait gates. Waiting runs release their worker lease;
  a signal wakes the run without losing completed checkpoints.
- Handle signal-before-wait delivery, duplicate delivery, and the release race transactionally.
- Show waiting tasks in the local inspector and add a CLI signal command and approval example.
- Refresh the public recovery lab with a visual trace, responsive hero, direct navigation to
  the demo, and an accessible event rail for jumping between recovery phases.
- Add an interactive durable-signals lab showing wait, early delivery, worker release, and resume.
- Refine the Retrace mark across the website, favicon, and local inspector.
- Add selectable red, yellow, and blue website palettes while retaining the original green,
  with independent light/dark preference and local persistence.
- Add search to the recovery event journal, combinable with the selected-step filter.

## 0.7.1 — 2026-09-28

- Add a worker-epoch view to the local inspector so ownership handoffs, retries, and
  interrupted attempts are visible alongside the graph and attempt timeline.
- Preserve the selected view in permalinks and keep the visualization usable on mobile.

## 0.7.0 — 2026-09-28

- Refresh the public playground theme with clearer visual hierarchy and responsive spacing.
- Add a dark theme switch that follows the system preference and saves the visitor's choice.
- Add bounded graceful worker draining on SIGTERM: stop new claims, finish active runs within
  a configurable grace period, and pause unfinished work for immediate local handoff.

## 0.6.0 — 2026-09-27

- Add durable, idempotent cancellation for queued, running, and paused runs via Python and CLI.
- Revoke ownership immediately, fence stale workers, and preserve completed checkpoints and
  interrupted attempt history for inspection.
- Show cancelled tasks and runs in the local inspector; document the cancellation contract.

## 0.5.0 — 2026-09-26

- Add idempotent submission keys: concurrent producers resolve to one durable run, while
  conflicting workflow definitions or inputs fail explicitly.
- Add delayed dispatch with persisted eligibility timestamps and manual-resume override.
- Migrate v1 SQLite databases to v2 in a transaction, preserving runs and checkpoints.
- Document the new queue contract and surface it in the public playground.

## 0.4.0 — 2026-09-25

- Add a local multi-process worker pool with atomic queue claims, bounded parallel runs,
  expired-lease takeover, and stale-worker fencing.
- Add process-level contention tests and a worker ownership view in the playground.

## 0.3.0 — 2026-09-17

- Five-minute onboarding, tested CSV ingestion and idempotent HTTP delivery examples.
- Prepared manual trusted PyPI publishing and contributor starter tasks.

- Inspector permalinks preserve the selected run, step, and timeline view across reloads and browser history.
- Missing run links display an explicit message while keeping other runs accessible.

- Download complete execution traces directly from the inspector.
- Redesigned checkpoint-and-recovery logo for the inspector, favicon, and README.

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
