"""Dependency-free Prometheus gauges from a coherent, read-only queue snapshot."""

from __future__ import annotations

from retrace.store import Store

CONTENT_TYPE = "text/plain; version=0.0.4"


def _labels(**values: str) -> str:
    def escape(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")

    return "{" + ",".join(f'{key}="{escape(value)}"' for key, value in values.items()) + "}"


def prometheus_metrics(store: Store) -> str:
    """Export current queue state, never lifetime counters or application payloads.

    Unlimited limits and absent eligible waits have no sample. Labels identify exact
    workflow definitions, not individual runs. Collection does not claim or resume work.
    """
    with store.transaction(immediate=False):
        queues = store.queue_stats()
    lines = [
        "# HELP retrace_queue_definitions Number of stored workflow definitions or queue policies.",
        "# TYPE retrace_queue_definitions gauge",
        f"retrace_queue_definitions {len(queues)}",
        "# HELP retrace_queue_runs Current run count by scheduling state and workflow definition.",
        "# TYPE retrace_queue_runs gauge",
    ]
    for item in queues:
        for state in ("ready", "delayed", "paused", "waiting", "active", "recoverable"):
            labels = _labels(workflow=item["name"], fingerprint=item["fingerprint"], state=state)
            lines.append(f"retrace_queue_runs{labels} {item[state]}")
    lines.extend(
        (
            "# HELP retrace_queue_limit Configured active or queued capacity; unlimited omitted.",
            "# TYPE retrace_queue_limit gauge",
        )
    )
    for item in queues:
        for resource in ("active", "queued"):
            limit = item[f"max_{resource}"]
            if limit is not None:
                labels = _labels(
                    workflow=item["name"], fingerprint=item["fingerprint"], resource=resource
                )
                lines.append(f"retrace_queue_limit{labels} {limit}")
    lines.extend(
        (
            "# HELP retrace_queue_oldest_eligible_seconds Oldest ready, paused or expired run age.",
            "# TYPE retrace_queue_oldest_eligible_seconds gauge",
        )
    )
    for item in queues:
        age = item["oldest_ready_age_seconds"]
        if age is not None:
            labels = _labels(workflow=item["name"], fingerprint=item["fingerprint"])
            lines.append(f"retrace_queue_oldest_eligible_seconds{labels} {age}")
    return "\n".join(lines) + "\n"
