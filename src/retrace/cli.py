"""Command-line entry point. Workflow imports execute trusted Python code."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import sqlite3
import sys
from dataclasses import asdict
from pathlib import Path

from retrace import DefinitionMismatch, Engine, LeaseLost, RunBusy, Store, Workflow, __version__


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
    commands.add_parser("runs", help="list recent runs as JSON")
    inspect = commands.add_parser("inspect", help="show run checkpoints and attempts as JSON")
    inspect.add_argument("run_id")
    events = commands.add_parser(
        "events", help="export the event journal as newline-delimited JSON"
    )
    events.add_argument("run_id")
    events.add_argument("--after", type=int, default=0, help="exclusive event cursor")
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
        readonly = args.command in ("runs", "inspect", "events") or (
            args.command == "retry" and args.dry_run
        )
        with Store(args.db, readonly=readonly) as store:
            if args.command == "runs":
                print(json.dumps(store.runs(), indent=2))
            elif args.command == "inspect":
                print(
                    json.dumps(
                        {
                            "run": store.run(args.run_id),
                            "tasks": store.tasks(args.run_id),
                            "attempts": store.history(args.run_id),
                        },
                        indent=2,
                    )
                )
            elif args.command == "events":
                store.run(args.run_id)
                cursor = args.after
                while page := store.events(args.run_id, after=cursor):
                    for event in page:
                        print(json.dumps(event))
                    cursor = page[-1]["id"]
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
    ) as exc:
        print(f"retrace: {exc}", file=sys.stderr)
        return 2
    return 0
