export const steps = [
  {
    name: "Read orders",
    key: "extract",
    description: "Read a small CSV file and checkpoint the input snapshot.",
    output: { rows: 128, source: "sample-orders.csv" },
  },
  {
    name: "Validate",
    key: "validate",
    description: "Check unique order IDs and nonnegative integer amounts.",
    output: { valid: 128, rejected: 0 },
  },
  {
    name: "Summarize",
    key: "summarize",
    description:
      "Calculate an exact total. The sample worker stops before its first result commits.",
    output: { orders: 128, total_cents: 384000 },
  },
  {
    name: "Publish report",
    key: "publish",
    description:
      "Finish the sample pipeline. Real external writes need downstream idempotency.",
    output: { report: "orders-summary.json", published: true },
  },
];
export const events = [
  {
    kind: "run.created",
    task: "run",
    text: "A fresh workflow is ready.",
    time: "00.000",
  },
  {
    kind: "task.started",
    task: "extract",
    text: "Reading the order snapshot.",
    time: "00.120",
  },
  {
    kind: "task.succeeded",
    task: "extract",
    text: "128 rows committed. Validation started.",
    time: "00.480",
  },
  {
    kind: "task.succeeded",
    task: "validate",
    text: "Validated rows committed. Summarize started.",
    time: "00.720",
  },
  {
    kind: "worker.interrupted",
    task: "summarize",
    text: "Worker stopped before the summary committed.",
    time: "00.950",
  },
  {
    kind: "run.claimed",
    task: "summarize",
    text: "After lease expiry, worker epoch 2 reuses two checkpoints.",
    time: "16.000",
  },
  {
    kind: "task.succeeded",
    task: "summarize",
    text: "Summary committed on attempt 2. Publishing started.",
    time: "16.300",
  },
  {
    kind: "run.succeeded",
    task: "publish",
    text: "All four checkpoints are committed.",
    time: "16.540",
  },
];
export function snapshot(phase: number) {
  if (!Number.isInteger(phase) || phase < 0 || phase > 7)
    throw new Error("Phase must be an integer from 0 to 7");
  const finished = [phase >= 2, phase >= 3, phase >= 6, phase >= 7];
  const active =
    phase === 0
      ? -1
      : phase === 1
        ? 0
        : phase === 2
          ? 1
          : phase <= 5
            ? 2
            : phase === 6
              ? 3
              : -1;
  return {
    phase,
    completed: finished.filter(Boolean).length,
    epoch: phase >= 5 ? 2 : 1,
    reused: phase >= 5 ? 2 : 0,
    status:
      phase === 0
        ? "Ready"
        : phase === 4
          ? "Interrupted"
          : phase === 7
            ? "Succeeded"
            : "Running",
    tasks: steps.map((step, i) => ({
      ...step,
      output: finished[i] ? step.output : null,
      status: finished[i]
        ? "committed"
        : active === i
          ? phase === 4
            ? "interrupted"
            : "running"
          : "pending",
      attempts:
        i === 0
          ? phase >= 1
            ? 1
            : 0
          : i === 1
            ? phase >= 2
              ? 1
              : 0
            : i === 2
              ? phase >= 5
                ? 2
                : phase >= 3
                  ? 1
                  : 0
              : phase >= 6
                ? 1
                : 0,
    })),
    events: events.slice(0, phase + 1),
  };
}
export function nextPhase(phase: number) {
  snapshot(phase);
  return Math.min(7, phase + 1);
}
