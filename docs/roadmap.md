# Roadmap

This is a direction, not a delivery promise. v0.1 is focused on inspectable local durability.

## Small, well-scoped contributions

- **Inspector event filters.** Filter by task and event kind without changing event ordering or
  dropping incoming events. Include empty states, keyboard support, and a browser test.
- **Graph navigation.** Add fit-to-view and zoom controls while preserving readable labels and
  keyboard access. Large graphs should not overflow the page itself.
- **Retry examples.** Demonstrate a deduplicated HTTP mutation against a small local test server.
  Show the side-effect-before-checkpoint crash window explicitly.
- **Trace export.** Convert attempt history to a documented trace format, preserving failed and
  interrupted attempts. Round-trip/check the timestamps in tests.

## Design proposals welcome

- **Selective retry.** Reset failed steps while preserving the audit log and invalidating the
  correct descendant closure. Decide how attempt budgets, versions, and terminal run identity work.
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
