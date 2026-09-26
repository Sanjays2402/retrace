# Roadmap

This is a direction, not a delivery promise. Retrace focuses on inspectable local durability.

## Implemented since v0.1

- Selective failed-branch retry with read-only preview, fresh failure budgets, and preserved history.
- Inspector journal filters, payload search/expansion, and filtered JSONL export without losing incoming events.

- Graph zoom, responsive fit-to-view, and reset controls with keyboard access.
- Chrome Trace JSON export with task lanes and preserved attempt history.
- A local multi-process worker pool with atomic queue claims, bounded parallel runs, and
  automatic recovery after a worker's lease expires.
- Idempotent submission keys and delayed run eligibility with a v1-to-v2 schema migration.

## Small, well-scoped contributions

- **Retry examples.** Demonstrate a deduplicated HTTP mutation against a small local test server.
  Show the side-effect-before-checkpoint crash window explicitly.

## Design proposals welcome

- **Retry classification and jitter.** Persist the selected retry deadline, with a deterministic
  test hook and clear treatment of nonretryable errors.
- **Retention and compaction.** Define what can be removed without breaking checkpoint integrity,
  incremental event cursors, or the recovery contract.
- **Storage evolution.** Transactional schema migrations with backup/rollback guidance and
  compatibility fixtures from prior releases.
- **Scheduler efficiency.** Replace repeated full task snapshots with an incremental ready queue.
  Benchmark before/after, preserve crash semantics, and keep storage as the recovery authority.

## Intentionally outside the current scope

Multi-host scheduling, a hosted control plane, arbitrary-code isolation, exactly-once external
side effects, and replacing a full orchestration platform. These need different operational and
security contracts and should not be implied by the current API.
