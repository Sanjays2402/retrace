# Is Retrace a fit?

Choose Retrace when you own a small async Python pipeline, can keep its SQLite database
on local disk, want completed steps to survive process restarts, and prefer no background
services. It is useful for batch imports, document ingestion, and local automation.

| Need | Retrace today |
| --- | --- |
| Resume a process after a crash | Supported with expired leases and preserved checkpoints |
| Inspect retries and step outputs locally | Included read-only inspector |
| Scale one run across many machines | Not supported; use a distributed workflow platform |
| Schedule recurring jobs | Bring an external scheduler; no built-in scheduler daemon |
| Run large CPU tasks | Use a separate process or service; keep the event loop responsive |
| Exactly-once external API calls | Requires downstream deduplication; tasks are at least once |
| Shared multi-tenant dashboard | Not supported; inspector is loopback-only |
| Long-lived stable API | Early alpha; pin versions and read release notes |

Read [architecture](architecture.md), [recovery rules](recovery.md), and the
[benchmark methodology](benchmark.md) before relying on it. Benchmarks describe one
machine and workload, not a general throughput guarantee.
