"""Chrome Trace Event export from a coherent, read-only run snapshot."""

from __future__ import annotations

import time

from retrace.store import Store


def export_trace(store: Store, run_id: str) -> dict:
    """Export attempt spans; unfinished attempts are instants, never invented durations.

    Task inputs, outputs, and exception messages are deliberately excluded.
    Each task gets its own lane so concurrent steps do not overlap on one track.
    """
    with store.transaction(immediate=False):
        run = store.run(run_id)
        attempts = store.history(run_id)
    lanes = {task["name"]: i + 1 for i, task in enumerate(run["manifest"]["tasks"])}
    events = [
        {"ph": "M", "name": "process_name", "pid": 1, "tid": 0, "args": {"name": run["name"]}}
    ]
    for name, lane in lanes.items():
        events.append(
            {"ph": "M", "name": "thread_name", "pid": 1, "tid": lane, "args": {"name": name}}
        )
    for attempt in attempts:
        event = {
            "name": attempt["task_name"],
            "cat": "retrace.attempt",
            "pid": 1,
            "tid": lanes[attempt["task_name"]],
            "ts": round((attempt["started_at"] - run["created_at"]) * 1_000_000),
            "args": {
                "attempt": attempt["number"],
                "epoch": attempt["epoch"],
                "status": attempt["status"],
            },
        }
        if attempt["finished_at"] is None:
            event.update(ph="I", s="t")
        else:
            event.update(
                ph="X",
                dur=max(0, round((attempt["finished_at"] - attempt["started_at"]) * 1_000_000)),
            )
        events.append(event)
    return {
        "traceEvents": events,
        "displayTimeUnit": "ms",
        "retrace": {
            "run_id": run_id,
            "status": run["status"],
            "origin_unix_seconds": run["created_at"],
            "exported_at": time.time(),
            "unfinished_attempts": "instant events",
        },
    }
