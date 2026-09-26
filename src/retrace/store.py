"""SQLite checkpoints, an append-only event journal, and fenced run leases.

Every checkpoint and its event commit together. BEGIN IMMEDIATE serializes
ownership changes across processes; an epoch fences a previous owner out.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from graphlib import TopologicalSorter
from pathlib import Path
from typing import Any

from retrace.workflow import Workflow, encode


class RunBusy(RuntimeError):
    """A live worker already owns this run."""


class LeaseLost(RuntimeError):
    """This worker no longer has permission to commit results."""


class DefinitionMismatch(ValueError):
    """Resuming with a changed workflow would invalidate checkpoints."""


@dataclass(frozen=True)
class Lease:
    run_id: str
    owner: str
    epoch: int


@dataclass(frozen=True)
class RetryPlan:
    """A point-in-time preview; execution always revalidates inside a write transaction."""

    run_id: str
    selected: tuple[str, ...]
    reset: tuple[str, ...]
    preserved: tuple[str, ...]
    remaining_failed: tuple[str, ...]
    remaining_blocked: tuple[str, ...]


_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, version TEXT NOT NULL,
    fingerprint TEXT NOT NULL, manifest TEXT NOT NULL, input TEXT NOT NULL,
    status TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
    owner TEXT, epoch INTEGER NOT NULL DEFAULT 0, lease_until REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS tasks (
    run_id TEXT NOT NULL REFERENCES runs(id), name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
    failures INTEGER NOT NULL DEFAULT 0, output TEXT, error TEXT,
    next_at REAL NOT NULL DEFAULT 0, started_at REAL, finished_at REAL,
    PRIMARY KEY (run_id, name)
);
CREATE TABLE IF NOT EXISTS attempts (
    run_id TEXT NOT NULL, task_name TEXT NOT NULL, number INTEGER NOT NULL,
    epoch INTEGER NOT NULL, status TEXT NOT NULL, started_at REAL NOT NULL,
    finished_at REAL, error TEXT,
    PRIMARY KEY (run_id, task_name, number),
    FOREIGN KEY (run_id, task_name) REFERENCES tasks(run_id, name)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(id),
    task_name TEXT, kind TEXT NOT NULL, at REAL NOT NULL, payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_run_id ON events(run_id, id);
CREATE INDEX IF NOT EXISTS runs_created_at ON runs(created_at DESC);
"""


class Store:
    """One connection per thread. Keep the database on a local filesystem."""

    def __init__(self, path: str | Path = "retrace.db", *, readonly: bool = False):
        self.path = str(path)
        deadline = time.monotonic() + 15
        while True:
            try:
                self._open(readonly)
                return
            except sqlite3.OperationalError as exc:
                if hasattr(self, "db"):
                    self.db.close()
                if "locked" not in str(exc).lower() or time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)

    def _open(self, readonly: bool) -> None:
        if readonly:
            uri = Path(self.path).resolve().as_uri() + "?mode=ro"
            self.db = sqlite3.connect(uri, uri=True, isolation_level=None, timeout=5)
        else:
            self.db = sqlite3.connect(self.path, isolation_level=None, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1, 2):
            self.db.close()
            raise ValueError(f"unsupported database schema version: {version}")
        if not readonly:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            if version == 0:
                self.db.executescript(_SCHEMA)
            if version < 2:
                with self.transaction():
                    columns = {row["name"] for row in self.db.execute("PRAGMA table_info(runs)")}
                    if "submission_key_hash" not in columns:
                        self.db.execute("ALTER TABLE runs ADD COLUMN submission_key_hash TEXT")
                    if "ready_at" not in columns:
                        self.db.execute(
                            "ALTER TABLE runs ADD COLUMN ready_at REAL NOT NULL DEFAULT 0"
                        )
                    self.db.execute(
                        """CREATE UNIQUE INDEX IF NOT EXISTS runs_submission_key
                        ON runs(submission_key_hash)"""
                    )
                    self.db.execute(
                        """CREATE INDEX IF NOT EXISTS runs_dispatch
                        ON runs(fingerprint,status,ready_at,created_at)"""
                    )
                    self.db.execute("PRAGMA user_version=2")

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    @contextmanager
    def transaction(self, *, immediate: bool = True) -> Iterator[None]:
        self.db.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def _event(self, run_id: str, kind: str, task: str | None = None, **payload: Any) -> None:
        self.db.execute(
            "INSERT INTO events(run_id,task_name,kind,at,payload) VALUES(?,?,?,?,?)",
            (run_id, task, kind, time.time(), encode(payload)),
        )

    def create(
        self,
        workflow: Workflow,
        input: Any = None,
        *,
        run_id: str | None = None,
        key: str | None = None,
        ready_at: float | None = None,
    ) -> str:
        """Persist a run; a matching key returns its original ID without changing it.

        ``ready_at`` is a Unix timestamp for worker dispatch only. Direct resume
        may claim the run before then. The first submission fixes the schedule.
        """
        caller_run_id = run_id
        run_id = uuid.uuid4().hex if run_id is None else run_id
        if not run_id or len(run_id) > 128:
            raise ValueError("run_id must contain 1–128 characters")
        if key is not None and (not isinstance(key, str) or not key or len(key) > 128):
            raise ValueError("key must contain 1–128 characters")
        data = encode(input)
        now = time.time()
        ready_at = now if ready_at is None else ready_at
        if (
            isinstance(ready_at, bool)
            or not isinstance(ready_at, (int, float))
            or not math.isfinite(ready_at)
            or ready_at < 0
        ):
            raise ValueError("ready_at must be a finite nonnegative timestamp")
        key_hash = hashlib.sha256(key.encode("utf-8")).hexdigest() if key is not None else None
        with self.transaction():
            if key_hash is not None:
                existing = self.db.execute(
                    "SELECT id,fingerprint,input FROM runs WHERE submission_key_hash=?",
                    (key_hash,),
                ).fetchone()
                if existing is not None:
                    if existing["fingerprint"] != workflow.fingerprint or existing["input"] != data:
                        raise ValueError("submission key is already used for different work")
                    if caller_run_id is not None and existing["id"] != caller_run_id:
                        raise ValueError("submission key already belongs to another run ID")
                    return existing["id"]
            self.db.execute(
                """INSERT INTO runs(id,name,version,fingerprint,manifest,input,status,
                created_at,updated_at,submission_key_hash,ready_at)
                VALUES(?,?,?,?,?,?,'pending',?,?,?,?)""",
                (
                    run_id,
                    workflow.name,
                    workflow.version,
                    workflow.fingerprint,
                    encode(workflow.manifest),
                    data,
                    now,
                    now,
                    key_hash,
                    ready_at,
                ),
            )
            self.db.executemany(
                "INSERT INTO tasks(run_id,name) VALUES(?,?)",
                [(run_id, task.name) for task in workflow.tasks],
            )
            self._event(run_id, "run.created", ready_at=ready_at)
        return run_id

    def run(self, run_id: str) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown run: {run_id}")
        result = dict(row)
        for key in ("input", "manifest"):
            result[key] = json.loads(result[key])
        return result

    def tasks(self, run_id: str) -> dict[str, dict[str, Any]]:
        result = {}
        for row in self.db.execute("SELECT * FROM tasks WHERE run_id=? ORDER BY name", (run_id,)):
            task = dict(row)
            task["output"] = json.loads(task["output"]) if task["output"] is not None else None
            result[task["name"]] = task
        return result

    def runs(self, limit: int = 100) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.db.execute(
                """SELECT id,name,version,status,created_at,updated_at,lease_until,epoch
            FROM runs ORDER BY created_at DESC LIMIT ?""",
                (min(max(limit, 1), 1000),),
            )
        ]

    def events(self, run_id: str, after: int = 0, limit: int = 500) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT * FROM events WHERE run_id=? AND id>? ORDER BY id LIMIT ?",
            (run_id, after, min(max(limit, 1), 1000)),
        )
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def history(self, run_id: str) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.db.execute(
                "SELECT * FROM attempts WHERE run_id=? ORDER BY started_at,task_name,number",
                (run_id,),
            )
        ]

    def _retry_plan(
        self, workflow: Workflow, run_id: str, task_names: Sequence[str] | None
    ) -> RetryPlan:
        run = self.run(run_id)
        if run["fingerprint"] != workflow.fingerprint:
            raise DefinitionMismatch("workflow changed; retry requires the original definition")
        if run["owner"] and run["lease_until"] > time.time():
            raise RunBusy(f"run {run_id} has a live worker")
        if run["status"] != "failed":
            raise ValueError("only failed runs can be retried; use resume for interrupted runs")
        states = self.tasks(run_id)
        failed = {name for name, state in states.items() if state["status"] == "failed"}
        selected = tuple(sorted(failed)) if task_names is None else tuple(task_names)
        if isinstance(task_names, str) or not selected or len(set(selected)) != len(selected):
            raise ValueError("select one or more unique failed task names")
        if set(selected) - failed:
            raise ValueError("retry selections must name currently failed tasks")
        graph = {task.name: task.needs for task in workflow.tasks}
        reset = set(selected)
        for name in TopologicalSorter(graph).static_order():
            # A fan-in remains blocked until *all* its dependencies can make progress.
            if states[name]["status"] == "blocked" and all(
                dep in reset or states[dep]["status"] == "succeeded" for dep in graph[name]
            ):
                reset.add(name)
        return RetryPlan(
            run_id=run_id,
            selected=tuple(sorted(selected)),
            reset=tuple(sorted(reset)),
            preserved=tuple(sorted(n for n, s in states.items() if s["status"] == "succeeded")),
            remaining_failed=tuple(sorted(failed - reset)),
            remaining_blocked=tuple(
                sorted(n for n, s in states.items() if s["status"] == "blocked" and n not in reset)
            ),
        )

    def retry_plan(
        self, workflow: Workflow, run_id: str, task_names: Sequence[str] | None = None
    ) -> RetryPlan:
        """Preview selective retry without changing the database; supports read-only stores."""
        with self.transaction(immediate=False):
            return self._retry_plan(workflow, run_id, task_names)

    def retry_failed(
        self, workflow: Workflow, run_id: str, task_names: Sequence[str] | None = None
    ) -> RetryPlan:
        """Atomically reopen a failed run, preserving checkpoints and historical attempts.

        The per-task failure budget restarts for reset tasks. Attempt numbers and
        idempotency keys do not restart. The caller must resume the pending run.
        """
        with self.transaction():
            plan = self._retry_plan(workflow, run_id, task_names)
            states = self.tasks(run_id)
            for name in plan.reset:
                self.db.execute(
                    """UPDATE tasks SET status='pending',failures=0,error=NULL,output=NULL,
                    next_at=0,started_at=NULL,finished_at=NULL WHERE run_id=? AND name=?""",
                    (run_id, name),
                )
                self._event(
                    run_id,
                    "task.reset",
                    name,
                    reason="manual_retry",
                    previous_status=states[name]["status"],
                    previous_failures=states[name]["failures"],
                )
            self.db.execute(
                """UPDATE runs SET status='pending',owner=NULL,lease_until=0,updated_at=?
                WHERE id=?""",
                (time.time(), run_id),
            )
            self._event(run_id, "run.retry_requested", selected=plan.selected, reset=plan.reset)
            return plan

    def claim(self, run_id: str, workflow: Workflow, ttl: float) -> Lease | None:
        with self.transaction():
            return self._claim_locked(run_id, workflow, ttl)

    def claim_next(
        self, workflow: Workflow, ttl: float, *, exclude: Sequence[str] = ()
    ) -> Lease | None:
        """Atomically claim the oldest eligible run for this exact workflow definition.

        The selection and ownership change share one write transaction, so competing
        processes cannot both acquire a pending or expired run.
        """
        excluded = tuple(exclude)
        with self.transaction():
            now = time.time()
            query = """SELECT id FROM runs WHERE fingerprint=?
                AND status IN ('pending','paused','running')
                AND (owner IS NULL OR lease_until<=?)
                AND (status!='pending' OR ready_at<=?)"""
            parameters: list[Any] = [workflow.fingerprint, now, now]
            if excluded:
                query += f" AND id NOT IN ({','.join('?' for _ in excluded)})"
                parameters.extend(excluded)
            query += " ORDER BY created_at,id LIMIT 1"
            row = self.db.execute(query, parameters).fetchone()
            return None if row is None else self._claim_locked(row["id"], workflow, ttl)

    def _claim_locked(self, run_id: str, workflow: Workflow, ttl: float) -> Lease | None:
        run = self.run(run_id)
        if run["fingerprint"] != workflow.fingerprint:
            raise DefinitionMismatch(
                "workflow changed; restore the original definition or start a new run"
            )
        if run["status"] in ("succeeded", "failed"):
            return None
        now = time.time()
        if run["owner"] and run["lease_until"] > now:
            raise RunBusy(f"run {run_id} has a live worker until {run['lease_until']:.3f}")
        lease = Lease(run_id, uuid.uuid4().hex, run["epoch"] + 1)
        self.db.execute(
            """UPDATE runs SET status='running',owner=?,epoch=?,lease_until=?,updated_at=?,
            ready_at=0
            WHERE id=?""",
            (lease.owner, lease.epoch, now + ttl, now, run_id),
        )
        interrupted = self.db.execute(
            "SELECT name FROM tasks WHERE run_id=? AND status='running'",
            (run_id,),
        ).fetchall()
        for row in interrupted:
            self._event(run_id, "task.interrupted", row["name"], reason="worker lease expired")
        self.db.execute(
            """UPDATE attempts SET status='interrupted',finished_at=?
            WHERE run_id=? AND status='running'""",
            (now, run_id),
        )
        self.db.execute(
            "UPDATE tasks SET status='pending' WHERE run_id=? AND status='running'",
            (run_id,),
        )
        self._event(run_id, "run.claimed", epoch=lease.epoch, recovered=len(interrupted))
        return lease

    def _fence(self, lease: Lease) -> None:
        row = self.db.execute(
            "SELECT owner,epoch,lease_until FROM runs WHERE id=?",
            (lease.run_id,),
        ).fetchone()
        if (
            row is None
            or row["owner"] != lease.owner
            or row["epoch"] != lease.epoch
            or row["lease_until"] <= time.time()
        ):
            raise LeaseLost(f"lease lost for run {lease.run_id}")

    def heartbeat(self, lease: Lease, ttl: float) -> None:
        with self.transaction():
            self._fence(lease)
            self.db.execute(
                "UPDATE runs SET lease_until=? WHERE id=?", (time.time() + ttl, lease.run_id)
            )

    def start_task(self, lease: Lease, name: str) -> int:
        with self.transaction():
            self._fence(lease)
            now = time.time()
            changed = self.db.execute(
                """UPDATE tasks SET status='running',attempts=attempts+1,started_at=?,
                finished_at=NULL,error=NULL WHERE run_id=? AND name=?
                AND status IN ('pending','retrying') AND next_at<=?""",
                (now, lease.run_id, name, now),
            ).rowcount
            if changed != 1:
                raise RuntimeError(f"task {name} is not ready")
            attempt = self.db.execute(
                "SELECT attempts FROM tasks WHERE run_id=? AND name=?",
                (lease.run_id, name),
            ).fetchone()[0]
            self.db.execute(
                "INSERT INTO attempts VALUES(?,?,?,?,'running',?,NULL,NULL)",
                (lease.run_id, name, attempt, lease.epoch, now),
            )
            self._event(lease.run_id, "task.started", name, attempt=attempt)
            return attempt

    def finish_task(
        self,
        lease: Lease,
        name: str,
        *,
        output: Any = None,
        error: str | None = None,
        retry_at: float | None = None,
    ) -> None:
        data = encode(output) if error is None else None
        with self.transaction():
            self._fence(lease)
            now = time.time()
            status = (
                "succeeded" if error is None else ("retrying" if retry_at is not None else "failed")
            )
            changed = self.db.execute(
                """UPDATE tasks SET status=?,output=?,error=?,next_at=?,finished_at=?,
                failures=failures+? WHERE run_id=? AND name=? AND status='running'""",
                (
                    status,
                    data,
                    error,
                    retry_at or 0,
                    now,
                    int(error is not None),
                    lease.run_id,
                    name,
                ),
            ).rowcount
            if changed != 1:
                raise RuntimeError(f"task {name} is not running")
            self.db.execute(
                """UPDATE attempts SET status=?,finished_at=?,error=?
                WHERE run_id=? AND task_name=? AND status='running'""",
                ("succeeded" if error is None else "failed", now, error, lease.run_id, name),
            )
            self._event(lease.run_id, f"task.{status}", name, error=error, retry_at=retry_at)

    def block_task(self, lease: Lease, name: str) -> None:
        with self.transaction():
            self._fence(lease)
            changed = self.db.execute(
                """UPDATE tasks SET status='blocked',finished_at=? WHERE run_id=? AND name=?
                AND status IN ('pending','retrying')""",
                (time.time(), lease.run_id, name),
            ).rowcount
            if changed:
                self._event(lease.run_id, "task.blocked", name, reason="dependency failed")

    def release(self, lease: Lease, status: str) -> None:
        if status not in ("succeeded", "failed", "paused"):
            raise ValueError(f"invalid release status: {status}")
        with self.transaction():
            self._fence(lease)
            now = time.time()
            if status == "paused":
                self.db.execute(
                    """UPDATE attempts SET status='interrupted',finished_at=?
                    WHERE run_id=? AND status='running'""",
                    (now, lease.run_id),
                )
                self.db.execute(
                    "UPDATE tasks SET status='pending' WHERE run_id=? AND status='running'",
                    (lease.run_id,),
                )
            self.db.execute(
                "UPDATE runs SET status=?,owner=NULL,lease_until=0,updated_at=? WHERE id=?",
                (status, now, lease.run_id),
            )
            self._event(lease.run_id, f"run.{status}")
