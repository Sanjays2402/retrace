"""Workflow reliability metrics from a bounded, consistent run cohort."""

from __future__ import annotations

import math
import time
from collections import Counter

from retrace.store import Store


def workflow_health(store: Store, *, since: float = 0, limit: int = 10000) -> dict:
    """Measure runs created since a Unix timestamp; use at most the latest limit runs.

    Completion latency includes queueing, signals, downtime, and manual retries.
    This is a live creation-time cohort, not a count of completions in the period.
    """
    if (
        isinstance(since, bool)
        or not isinstance(since, (int, float))
        or not math.isfinite(since)
        or since < 0
    ):
        raise ValueError("since must be a finite nonnegative Unix timestamp")
    if type(limit) is not int or not 1 <= limit <= 10000:
        raise ValueError("limit must be an integer between 1 and 10000")
    captured_at = time.time()
    selected = "SELECT id FROM runs WHERE created_at>=? ORDER BY created_at DESC,id DESC LIMIT ?"
    with store.transaction(immediate=False):
        total = store.db.execute(
            "SELECT COUNT(*) FROM runs WHERE created_at>=?", (since,)
        ).fetchone()[0]
        rows = store.db.execute(
            f"SELECT name,version,fingerprint,status,created_at,updated_at,lease_until,id "
            f"FROM runs WHERE id IN ({selected}) ORDER BY created_at DESC,id DESC",
            (since, limit),
        ).fetchall()
        attempts = store.db.execute(
            f"SELECT run_id,status,COUNT(*) AS count FROM attempts WHERE run_id IN ({selected}) "
            "AND status IN ('failed','interrupted') GROUP BY run_id,status",
            (since, limit),
        ).fetchall()
    failures = Counter(
        {row["run_id"]: row["count"] for row in attempts if row["status"] == "failed"}
    )
    interruptions = Counter(
        {row["run_id"]: row["count"] for row in attempts if row["status"] == "interrupted"}
    )
    groups = {}
    for row in rows:
        key = (row["name"], row["version"], row["fingerprint"])
        groups.setdefault(key, []).append(row)
    workflows = []
    for (name, version, fingerprint), runs in sorted(groups.items()):
        statuses = dict(Counter(row["status"] for row in runs))
        completed = [row for row in runs if row["status"] in ("succeeded", "failed")]
        durations = sorted(max(0, row["updated_at"] - row["created_at"]) for row in completed)
        workflows.append(
            {
                "name": name,
                "version": version,
                "fingerprint": fingerprint,
                "runs": len(runs),
                "statuses": statuses,
                "completed_runs": len(completed),
                "failure_rate": statuses.get("failed", 0) / len(completed) if completed else None,
                "p50_completion_seconds": durations[math.ceil(0.5 * len(durations)) - 1]
                if durations
                else None,
                "p95_completion_seconds": durations[math.ceil(0.95 * len(durations)) - 1]
                if durations
                else None,
                "failed_attempts": sum(failures[row["id"]] for row in runs),
                "interrupted_attempts": sum(interruptions[row["id"]] for row in runs),
                "recovered_runs": sum(
                    row["status"] == "succeeded" and failures[row["id"]] > 0 for row in runs
                ),
                "expired_leases": sum(
                    row["status"] == "running" and row["lease_until"] <= captured_at for row in runs
                ),
            }
        )
    return {
        "since": since,
        "captured_at": captured_at,
        "matched_runs": total,
        "sampled_runs": len(rows),
        "limit": limit,
        "truncated": total > len(rows),
        "workflows": workflows,
    }
