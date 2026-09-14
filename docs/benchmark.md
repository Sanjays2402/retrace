# Local overhead baseline

Run `python scripts/benchmark.py --tasks 100 --repetitions 5` after installing Retrace.
Each repetition creates a fresh local SQLite database and runs 100 independent async no-op
steps at concurrency eight. Timing includes run creation, scheduling, durable task/attempt/event
writes, and completion; schema initialization occurs before the timer. Each result is checked.

[The initial measurement](benchmark-baseline.json) records the raw samples and environment:
Python 3.12.14 on Darwin arm64, SQLite WAL with `synchronous=FULL`, local temporary storage.
The median was about **60 ms for 100 steps** across five repetitions. This is a tiny-payload,
local-disk overhead measurement, not a throughput guarantee or a comparison with other engines.

No-op tasks amplify scheduler and disk overhead. Real I/O workloads, task payload sizes,
filesystem behavior, contention, journaling growth, CPU frequency, and graph shape can change
results substantially. The benchmark does not exercise external effects or simulate a power loss.
Use the script to compare changes on the same machine; retain raw samples and describe the workload.
