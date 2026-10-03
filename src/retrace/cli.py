"""Command-line entry point. Workflow imports execute trusted Python code."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import math
import signal
import sqlite3
import sys
import time
from dataclasses import asdict
from pathlib import Path

from retrace import (
    DefinitionMismatch,
    Engine,
    LeaseLost,
    QueueFull,
    RunBusy,
    Store,
    Workflow,
    __version__,
)


def load_workflow(spec: str) -> Workflow:
    module_name, separator, attribute = spec.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("workflow must be MODULE:ATTRIBUTE, e.g. examples.pipeline:workflow")
    sys.path.insert(0, str(Path.cwd()))
    try:
        workflow = getattr(importlib.import_module(module_name), attribute)
    finally:
        sys.path.pop(0)
    if not isinstance(workflow, Workflow):
        raise TypeError(f"{spec} is not a Workflow")
    return workflow


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Retrace · crash-resumable Python workflows")
    root.add_argument("--version", action="version", version=f"retrace {__version__}")
    root.add_argument(
        "--db", default="retrace.db", help="SQLite database path (default: retrace.db)"
    )
    root.add_argument("--concurrency", type=int, default=4)
    root.add_argument("--lease-ttl", type=float, default=15)
    commands = root.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="execute a trusted MODULE:ATTRIBUTE workflow")
    run.add_argument("workflow")
    run.add_argument("--input", default="null", help="JSON input")
    submit = commands.add_parser("submit", help="enqueue a workflow run for local workers")
    submit.add_argument("workflow")
    submit.add_argument("--input", default="null", help="JSON input")
    submit.add_argument("--key", help="idempotent submission key, unique within this database")
    submit.add_argument(
        "--delay",
        type=float,
        default=0,
        help="wait this many seconds before a worker claims the run",
    )
    worker = commands.add_parser("worker", help="claim and execute queued runs on this machine")
    worker.add_argument("workflow", nargs="+", help="one or more MODULE:ATTRIBUTE definitions")
    worker.add_argument("--max-runs", type=int, default=1, help="parallel runs in this process")
    worker.add_argument("--poll-interval", type=float, default=1.0, help="idle poll seconds")
    worker.add_argument("--once", action="store_true", help="drain available runs and exit")
    queue = commands.add_parser("queue", help="show queue depth, wait age, and policies")
    queue.add_argument(
        "--configure", metavar="WORKFLOW", help="persist limits for MODULE:ATTRIBUTE"
    )
    queue.add_argument("--max-active", type=int, help="max live runs across all local workers")
    queue.add_argument("--max-queued", type=int, help="max pending or paused runs")
    worker.add_argument(
        "--drain-timeout",
        type=float,
        default=30.0,
        help="seconds to finish active runs after SIGTERM before pausing them (default: 30)",
    )
    cancel = commands.add_parser("cancel", help="cancel a queued or active run by ID")
    cancel.add_argument("run_id")
    send_signal = commands.add_parser("signal", help="deliver a durable one-shot signal")
    send_signal.add_argument("run_id")
    send_signal.add_argument("name")
    send_signal.add_argument("--payload", default="null", help="JSON signal payload")
    resume = commands.add_parser("resume", help="resume an interrupted run")
    resume.add_argument("workflow")
    resume.add_argument("run_id")
    retry = commands.add_parser(
        "retry", help="retry failed steps, preserving successful checkpoints"
    )
    retry.add_argument("workflow")
    retry.add_argument("run_id")
    retry.add_argument(
        "--task", action="append", help="failed task to retry; repeat to select several"
    )
    retry.add_argument(
        "--dry-run", action="store_true", help="print the retry plan without executing"
    )
    demo = commands.add_parser("demo", help="run a document-indexing simulation")
    demo.add_argument(
        "--crash", action="store_true", help="hard-exit during embedding; resume afterward"
    )
    runs = commands.add_parser("runs", help="list recent runs as JSON")
    health = commands.add_parser("health", help="workflow failure rates and completion latency")
    health.add_argument(
        "--hours", type=float, default=24, help="creation-time lookback in hours (default: 24)"
    )
    health.add_argument(
        "--max-failure-rate", type=float, help="maximum failed/completed fraction (0–1)"
    )
    health.add_argument("--max-p95-seconds", type=float, help="maximum p95 completion elapsed time")
    health.add_argument(
        "--max-expired-leases", type=int, help="maximum expired leases per definition"
    )
    health.add_argument(
        "--min-completed",
        type=int,
        default=5,
        help="completed runs required for rate/latency checks (default: 5)",
    )
    runs.add_argument("--limit", type=int, default=100, help="maximum runs to list (default: 100)")
    runs.add_argument("--status", action="append", help="run status; repeat to match several")
    runs.add_argument("--workflow", help="exact workflow name, across versions")
    runs.add_argument("--before", metavar="RUN_ID", help="list runs older than this run")
    inspect = commands.add_parser("inspect", help="show run checkpoints and attempts as JSON")
    inspect.add_argument("run_id")
    events = commands.add_parser(
        "events", help="export the event journal as newline-delimited JSON"
    )
    events.add_argument("run_id")
    events.add_argument("--after", type=int, default=0, help="exclusive event cursor")
    trace = commands.add_parser("trace", help="export Chrome Trace JSON for Perfetto")
    trace.add_argument("run_id")
    report = commands.add_parser("report", help="export a diagnostic run report without payloads")
    report.add_argument("run_id")
    report.add_argument(
        "--include-payloads", action="store_true", help="include application data and errors"
    )
    report.add_argument(
        "--event-limit", type=int, default=1000, help="latest journal events (1–1000)"
    )
    backup = commands.add_parser("backup", help="save a checked standalone database snapshot")
    backup.add_argument("destination", help="new backup file; existing paths are never overwritten")
    backup.add_argument(
        "--timeout", type=float, default=30, help="copy timeout in seconds (default: 30)"
    )
    prune = commands.add_parser("prune", help="preview cleanup of old terminal unkeyed runs")
    prune.add_argument(
        "--older-than", type=float, required=True, metavar="DAYS", help="age in days"
    )
    prune.add_argument("--limit", type=int, default=100, help="max runs per batch (1–500)")
    prune.add_argument("--include-failed", action="store_true", help="also remove old failed runs")
    prune.add_argument(
        "--apply", action="store_true", help="delete eligible runs; default is preview"
    )
    serve = commands.add_parser("serve", help="open a read-only local dashboard")
    serve.add_argument("--port", type=int, default=7760)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "serve":
            from retrace.server import serve

            serve(args.db, args.port)
            return 0
        readonly = (
            args.command in ("runs", "inspect", "events", "trace", "report", "backup", "health")
            or (args.command == "queue" and args.configure is None)
            or (args.command == "retry" and args.dry_run)
            or (args.command == "prune" and not args.apply)
        )
        with Store(args.db, readonly=readonly) as store:
            if args.command == "runs":
                print(
                    json.dumps(
                        store.runs(
                            limit=args.limit,
                            statuses=args.status,
                            workflow=args.workflow,
                            before=args.before,
                        ),
                        indent=2,
                    )
                )
            elif args.command == "health":
                from retrace.health import evaluate_health, workflow_health

                if not math.isfinite(args.hours) or args.hours <= 0:
                    raise ValueError("hours must be finite and positive")
                if args.min_completed < 1:
                    raise ValueError("min-completed must be positive")
                metrics = workflow_health(store, since=max(0, time.time() - args.hours * 3600))
                if any(
                    value is not None
                    for value in (
                        args.max_failure_rate,
                        args.max_p95_seconds,
                        args.max_expired_leases,
                    )
                ):
                    metrics["checks"] = evaluate_health(
                        metrics,
                        max_failure_rate=args.max_failure_rate,
                        max_p95_seconds=args.max_p95_seconds,
                        max_expired_leases=args.max_expired_leases,
                        min_completed=args.min_completed,
                    )
                    print(json.dumps(metrics, indent=2))
                    return {"passed": 0, "failed": 1, "insufficient_data": 3}[
                        metrics["checks"]["status"]
                    ]
                print(json.dumps(metrics, indent=2))
            elif args.command == "inspect":
                print(
                    json.dumps(
                        {
                            "run": store.run(args.run_id),
                            "tasks": store.tasks(args.run_id),
                            "attempts": store.history(args.run_id),
                            "signals": store.signals(args.run_id),
                        },
                        indent=2,
                    )
                )
            elif args.command == "trace":
                from retrace.trace import export_trace

                print(json.dumps(export_trace(store, args.run_id), allow_nan=False))
            elif args.command == "report":
                from retrace.report import export_report

                print(
                    json.dumps(
                        export_report(
                            store,
                            args.run_id,
                            include_payloads=args.include_payloads,
                            event_limit=args.event_limit,
                        ),
                        indent=2,
                    )
                )
            elif args.command == "backup":
                print(
                    json.dumps(
                        asdict(store.backup(args.destination, timeout=args.timeout)), indent=2
                    )
                )
            elif args.command == "prune":
                if not math.isfinite(args.older_than) or args.older_than <= 0:
                    raise ValueError("--older-than must be a finite positive number of days")
                before = max(0, time.time() - args.older_than * 86400)
                operation = store.prune if args.apply else store.prune_plan
                plan = operation(before, limit=args.limit, include_failed=args.include_failed)
                print(json.dumps({"applied": args.apply, **asdict(plan)}, indent=2))
            elif args.command == "events":
                store.run(args.run_id)
                cursor = args.after
                while page := store.events(args.run_id, after=cursor):
                    for event in page:
                        print(json.dumps(event))
                    cursor = page[-1]["id"]
            elif args.command == "queue":
                if args.configure is not None:
                    if args.max_active is None and args.max_queued is None:
                        raise ValueError("--configure requires --max-active or --max-queued")
                    workflow = load_workflow(args.configure)
                    existing = next(
                        (
                            item
                            for item in store.queue_stats()
                            if item["fingerprint"] == workflow.fingerprint
                        ),
                        None,
                    )
                    store.configure_queue(
                        workflow,
                        max_active=args.max_active
                        if args.max_active is not None
                        else (existing["max_active"] if existing else None),
                        max_queued=args.max_queued
                        if args.max_queued is not None
                        else (existing["max_queued"] if existing else None),
                    )
                elif args.max_active is not None or args.max_queued is not None:
                    raise ValueError("queue limits require --configure WORKFLOW")
                print(json.dumps(store.queue_stats(), indent=2))
            elif args.command == "submit":
                workflow = load_workflow(args.workflow)
                if not math.isfinite(args.delay) or args.delay < 0:
                    raise ValueError("delay must be finite and nonnegative")
                run_id = store.create(
                    workflow,
                    json.loads(args.input),
                    key=args.key,
                    ready_at=time.time() + args.delay,
                )
                run = store.run(run_id)
                print(
                    json.dumps(
                        {"run_id": run_id, "status": run["status"], "ready_at": run["ready_at"]}
                    )
                )
            elif args.command == "cancel":
                changed = store.cancel(args.run_id)
                print(
                    json.dumps({"run_id": args.run_id, "status": "cancelled", "changed": changed})
                )
            elif args.command == "signal":
                changed = store.signal(args.run_id, args.name, json.loads(args.payload))
                print(json.dumps({"run_id": args.run_id, "name": args.name, "delivered": changed}))
            elif args.command == "worker":
                from retrace.worker import Worker

                workflows = tuple(load_workflow(spec) for spec in args.workflow)
                if not math.isfinite(args.drain_timeout) or args.drain_timeout < 0:
                    raise ValueError("drain timeout must be finite and nonnegative")
                dispatcher = Worker(
                    store,
                    workflows,
                    max_runs=args.max_runs,
                    concurrency=args.concurrency,
                    lease_ttl=args.lease_ttl,
                    poll_interval=args.poll_interval,
                )

                async def serve_worker():
                    stop = asyncio.Event()
                    previous = signal.getsignal(signal.SIGTERM)

                    def request_stop(_signum, _frame):
                        stop.set()

                    try:
                        signal.signal(signal.SIGTERM, request_stop)
                    except ValueError:
                        # Signal handlers require the main thread; the Python API
                        # can still stop a worker by passing stop_event directly.
                        previous = None
                    try:
                        return await dispatcher.serve(
                            once=args.once,
                            on_result=None
                            if args.once
                            else lambda result: print(json.dumps(asdict(result)), flush=True),
                            stop_event=stop,
                            drain_timeout=args.drain_timeout,
                        )
                    finally:
                        if previous is not None:
                            signal.signal(signal.SIGTERM, previous)

                results = asyncio.run(serve_worker())
                if args.once:
                    print(json.dumps([asdict(result) for result in results]))
            else:
                spec = "retrace.demo:workflow" if args.command == "demo" else args.workflow
                workflow = load_workflow(spec)
                engine = Engine(store, concurrency=args.concurrency, lease_ttl=args.lease_ttl)
                if args.command in ("resume", "retry"):
                    run_id = args.run_id
                else:
                    data = (
                        {"crash": args.crash} if args.command == "demo" else json.loads(args.input)
                    )
                    run_id = store.create(workflow, data)
                print(f"Run: {run_id}", file=sys.stderr, flush=True)
                if args.command == "demo" and args.crash:
                    print(
                        f"After the lease expires, resume with:\n  retrace --db {args.db!r} "
                        f"resume retrace.demo:workflow {run_id}",
                        file=sys.stderr,
                        flush=True,
                    )
                if args.command == "retry" and args.dry_run:
                    print(
                        json.dumps(asdict(store.retry_plan(workflow, run_id, args.task)), indent=2)
                    )
                    return 0
                execution = (
                    engine.retry(workflow, run_id, tasks=args.task)
                    if args.command == "retry"
                    else engine.resume(workflow, run_id)
                )
                result = asyncio.run(execution)
                print(json.dumps(asdict(result), indent=2))
                return 0 if result.status == "succeeded" else 1
    except KeyboardInterrupt:
        print(
            "Interrupted. Completed checkpoints are safe; resume using the run ID.", file=sys.stderr
        )
        return 130
    except (
        ValueError,
        TypeError,
        KeyError,
        ImportError,
        AttributeError,
        OSError,
        sqlite3.Error,
        DefinitionMismatch,
        RunBusy,
        LeaseLost,
        QueueFull,
    ) as exc:
        print(f"retrace: {exc}", file=sys.stderr)
        return 2
    return 0
