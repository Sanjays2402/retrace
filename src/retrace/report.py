"""Bounded diagnostic exports from a coherent read-only database snapshot."""

from __future__ import annotations

import json
import time

from retrace.store import Store


def export_report(
    store: Store, run_id: str, *, include_payloads: bool = False, event_limit: int = 1000
) -> dict:
    """Export checkpoints and history; omit application data unless explicitly requested.

    Owner tokens and submission key hashes are never exported. Names and timestamps
    remain visible. The journal is the latest event_limit events, in ascending order.
    """
    if type(include_payloads) is not bool:
        raise ValueError("include_payloads must be a boolean")
    if type(event_limit) is not int or not 1 <= event_limit <= 1000:
        raise ValueError("event_limit must be an integer between 1 and 1000")
    with store.transaction(immediate=False):
        run = store.run(run_id)
        tasks = store.tasks(run_id)
        attempts = store.history(run_id)
        signals = store.signals(run_id)
        total = store.db.execute(
            "SELECT COUNT(*) FROM events WHERE run_id=?", (run_id,)
        ).fetchone()[0]
        events = [
            dict(row)
            for row in store.db.execute(
                "SELECT * FROM events WHERE run_id=? ORDER BY id DESC LIMIT ?",
                (run_id, event_limit),
            )
        ][::-1]
        schema_version = store.schema_version
    run = {k: v for k, v in run.items() if k not in {"owner", "submission_key_hash"}}
    if not include_payloads:
        run.pop("input", None)
        tasks = {
            name: {k: v for k, v in task.items() if k not in {"output", "error"}}
            for name, task in tasks.items()
        }
        attempts = [{k: v for k, v in attempt.items() if k != "error"} for attempt in attempts]
        signals = [{k: v for k, v in signal.items() if k != "payload"} for signal in signals]
    for event in events:
        if include_payloads:
            event["payload"] = json.loads(event["payload"])
        else:
            event.pop("payload", None)
    return {
        "report_version": 1,
        "exported_at": time.time(),
        "schema_version": schema_version,
        "includes_payloads": include_payloads,
        "run": run,
        "tasks": tasks,
        "attempts": attempts,
        "signals": signals,
        "journal": {
            "events": events,
            "total": total,
            "limit": event_limit,
            "truncated": total > len(events),
        },
    }
