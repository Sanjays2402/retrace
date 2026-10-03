# Roadmap

This is a direction, not a delivery promise. Retrace focuses on inspectable local durability.

## Implemented since v0.1

- Downloadable diagnostic reports with default payload omission, coherent run snapshots,
  and explicit recent-journal truncation metadata.

- Expand live inspector history beyond the initial 100 runs, with bounded loading and
  filters across the loaded list.

- Filtered run history by status and workflow, with exclusive cursor pagination in Python,
  CLI, and the local inspector API.

- Selective failed-branch retry with read-only preview, fresh failure budgets, and preserved history.
- Inspector journal filters, payload search/expansion, and filtered JSONL export without losing incoming events.

- Graph zoom, responsive fit-to-view, and reset controls with keyboard access.
- Chrome Trace JSON export with task lanes and preserved attempt history.
- A local multi-process worker pool with atomic queue claims, bounded parallel runs, and
  automatic recovery after a worker's lease expires.
- Idempotent submission keys and delayed run eligibility with a v1-to-v2 schema migration.
- Durable cancellation for queued and active runs, with lease revocation and preserved checkpoints.
- Bounded graceful worker drain for local rolling restarts and immediate paused-run handoff.
- Exception-based retry classification and optional full jitter, with persisted deadlines
  reused across restarts and compatibility for default-policy workflows.
- Whole-run retention with read-only preview, bounded atomic deletion, and protection for
  unfinished work, submission keys, and retained event cursors.
- Checked online database backups, no-overwrite publication, and restore from saved checkpoints.

## Small, well-scoped contributions

- **Retry examples.** Demonstrate a deduplicated HTTP mutation against a small local test server.
  Show the side-effect-before-checkpoint crash window explicitly.

## Design proposals welcome

- **Keyed-run expiration and compaction.** Define a deduplication lifetime and partial-history
  contract beyond whole-run cleanup, without breaking event cursors or recovery.
- **Storage evolution.** Transactional schema migrations with backup/rollback guidance and
  compatibility fixtures from prior releases.
- **Scheduler efficiency.** Replace repeated full task snapshots with an incremental ready queue.
  Benchmark before/after, preserve crash semantics, and keep storage as the recovery authority.

## Intentionally outside the current scope

Multi-host scheduling, a hosted control plane, arbitrary-code isolation, exactly-once external
side effects, and replacing a full orchestration platform. These need different operational and
security contracts and should not be implied by the current API.
