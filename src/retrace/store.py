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

from retrace.workflow import _NAME, Workflow, encode


class RunBusy(RuntimeError):
    """A live worker already owns this run."""


class LeaseLost(RuntimeError):
    """This worker no longer has permission to commit results."""


class DefinitionMismatch(ValueError):
    """Resuming with a changed workflow would invalidate checkpoints."""


class QueueFull(RuntimeError):
    """A workflow's configured pending-run limit has been reached."""


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


@dataclass(frozen=True)
class PrunePlan:
    """Cleanup preview or receipt; previews do not reserve runs for deletion."""

    before: float
    run_ids: tuple[str, ...]
    tasks: int
    attempts: int
    events: int
    signals: int
    protected_keyed_runs: int


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
        if version not in (0, 1, 2, 3, 4):
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
            if version < 3:
                with self.transaction():
                    self.db.execute(
                        """CREATE TABLE IF NOT EXISTS signals (
                        run_id TEXT NOT NULL REFERENCES runs(id), name TEXT NOT NULL,
                        payload TEXT NOT NULL, received_at REAL NOT NULL,
                        PRIMARY KEY (run_id, name))"""
                    )
                    self.db.execute("PRAGMA user_version=3")
            if version < 4:
                with self.transaction():
                    self.db.execute(
                        """CREATE TABLE IF NOT EXISTS queue_policies (
                        fingerprint TEXT PRIMARY KEY, name TEXT NOT NULL,
                        max_active INTEGER, max_queued INTEGER,
                        last_claimed INTEGER NOT NULL DEFAULT 0)"""
                    )
                    self.db.execute(
                        """CREATE TABLE IF NOT EXISTS scheduler_clock (
                        id INTEGER PRIMARY KEY CHECK(id=1), tick INTEGER NOT NULL)"""
                    )
                    self.db.execute("INSERT OR IGNORE INTO scheduler_clock VALUES(1,0)")
                    self.db.execute("PRAGMA user_version=4")
        self.schema_version = self.db.execute("PRAGMA user_version").fetchone()[0]

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

    def _prune_plan(self, before: float, limit: int, include_failed: bool) -> PrunePlan:
        if self.schema_version < 4:
            raise ValueError("cleanup requires schema v4; open the database for writing to migrate")
        if (
            isinstance(before, bool)
            or not isinstance(before, (int, float))
            or not math.isfinite(before)
            or before < 0
        ):
            raise ValueError("before must be a finite nonnegative timestamp")
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("cleanup limit must be an integer between 1 and 500")
        if type(include_failed) is not bool:
            raise ValueError("include_failed must be a boolean")
        statuses = (
            ("succeeded", "cancelled", "failed") if include_failed else ("succeeded", "cancelled")
        )
        marks = ",".join("?" for _ in statuses)
        eligible = f"status IN ({marks}) AND updated_at<? AND owner IS NULL"
        parameters = (*statuses, before)
        protected = self.db.execute(
            f"SELECT COUNT(*) FROM runs WHERE {eligible} AND submission_key_hash IS NOT NULL",
            parameters,
        ).fetchone()[0]
        ids = tuple(
            row[0]
            for row in self.db.execute(
                f"SELECT id FROM runs WHERE {eligible} AND submission_key_hash IS NULL "
                "ORDER BY updated_at,id LIMIT ?",
                (*parameters, limit),
            )
        )
        counts = []
        for table in ("tasks", "attempts", "events", "signals"):
            placeholders = ",".join("?" for _ in ids)
            count = (
                self.db.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE run_id IN ({placeholders})", ids
                ).fetchone()[0]
                if ids
                else 0
            )
            counts.append(count)
        return PrunePlan(float(before), ids, *counts, protected)

    def prune_plan(
        self, before: float, *, limit: int = 100, include_failed: bool = False
    ) -> PrunePlan:
        """Preview whole terminal runs older than a Unix timestamp, without changing storage."""
        with self.transaction(immediate=False):
            return self._prune_plan(before, limit, include_failed)

    def prune(self, before: float, *, limit: int = 100, include_failed: bool = False) -> PrunePlan:
        """Atomically remove eligible unkeyed runs and all their dependent records.

        Select again under the write lock; active, unfinished, and keyed runs
        are always protected. Freed pages remain reusable inside SQLite.
        """
        with self.transaction():
            plan = self._prune_plan(before, limit, include_failed)
            for table in ("events", "signals", "attempts", "tasks", "runs"):
                column = "id" if table == "runs" else "run_id"
                self.db.executemany(
                    f"DELETE FROM {table} WHERE {column}=?", ((run_id,) for run_id in plan.run_ids)
                )
            return plan

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
            policy = self.db.execute(
                "SELECT max_queued FROM queue_policies WHERE fingerprint=?",
                (workflow.fingerprint,),
            ).fetchone()
            if policy is not None and policy["max_queued"] is not None:
                queued = self.db.execute(
                    """SELECT COUNT(*) FROM runs WHERE fingerprint=?
                    AND status IN ('pending','paused')""",
                    (workflow.fingerprint,),
                ).fetchone()[0]
                if queued >= policy["max_queued"]:
                    raise QueueFull(f"queue full for {workflow.name}: {queued} queued runs")
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

    def signal_payload(self, run_id: str, name: str) -> tuple[bool, Any]:
        """Return presence separately because JSON null is a valid signal payload."""
        row = self.db.execute(
            "SELECT payload FROM signals WHERE run_id=? AND name=?", (run_id, name)
        ).fetchone()
        return (False, None) if row is None else (True, json.loads(row["payload"]))

    def signals(self, run_id: str) -> list[dict[str, Any]]:
        self.run(run_id)
        if self.schema_version < 3:
            return []
        return [
            {**dict(row), "payload": json.loads(row["payload"])}
            for row in self.db.execute(
                """SELECT name,payload,received_at FROM signals
                WHERE run_id=? ORDER BY received_at,name""",
                (run_id,),
            )
        ]

    def signal(self, run_id: str, name: str, payload: Any = None) -> bool:
        """Deliver a one-shot signal; identical redelivery is a no-op.

        A signal may arrive before its gate is reached. Its immutable payload is
        retained across task retries and process crashes.
        """
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise ValueError("invalid signal name")
        data = encode(payload)
        with self.transaction():
            run = self.run(run_id)
            if not any(t.get("wait_for") == name for t in run["manifest"]["tasks"]):
                raise ValueError(f"workflow has no task waiting for signal {name!r}")
            existing = self.db.execute(
                "SELECT payload FROM signals WHERE run_id=? AND name=?", (run_id, name)
            ).fetchone()
            if existing is not None:
                if existing["payload"] != data:
                    raise ValueError("signal already delivered with a different payload")
                return False
            if run["status"] in ("succeeded", "failed", "cancelled"):
                raise ValueError("completed runs cannot receive new signals")
            now = time.time()
            self.db.execute(
                "INSERT INTO signals(run_id,name,payload,received_at) VALUES(?,?,?,?)",
                (run_id, name, data, now),
            )
            waiting = [t["name"] for t in run["manifest"]["tasks"] if t.get("wait_for") == name]
            woke = self.db.execute(
                f"UPDATE tasks SET status='pending' WHERE run_id=? AND status='waiting' "
                f"AND name IN ({','.join('?' for _ in waiting)})",
                (run_id, *waiting),
            ).rowcount
            if run["status"] == "waiting" and woke:
                self.db.execute(
                    "UPDATE runs SET status='pending',updated_at=? WHERE id=?",
                    (now, run_id),
                )
            self._event(run_id, "signal.received", name=name)
            return True

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

    def cancel(self, run_id: str) -> bool:
        """Atomically cancel an unfinished run and fence any current owner.

        Returns False when already cancelled. Successful checkpoints and all
        historical attempts remain available for inspection.
        """
        with self.transaction():
            run = self.run(run_id)
            if run["status"] == "cancelled":
                return False
            if run["status"] in ("succeeded", "failed"):
                raise ValueError("completed runs cannot be cancelled")
            now = time.time()
            running = self.db.execute(
                "SELECT name FROM tasks WHERE run_id=? AND status='running'", (run_id,)
            ).fetchall()
            for task in running:
                self._event(run_id, "task.interrupted", task["name"], reason="run cancelled")
            self.db.execute(
                """UPDATE attempts SET status='interrupted',finished_at=?
                WHERE run_id=? AND status='running'""",
                (now, run_id),
            )
            changed = self.db.execute(
                """UPDATE tasks SET status='cancelled',finished_at=?,next_at=0
                WHERE run_id=? AND status NOT IN ('succeeded','failed','cancelled')""",
                (now, run_id),
            ).rowcount
            self.db.execute(
                """UPDATE runs SET status='cancelled',owner=NULL,epoch=epoch+1,
                lease_until=0,ready_at=0,updated_at=? WHERE id=?""",
                (now, run_id),
            )
            self._event(run_id, "run.cancelled", interrupted=len(running), cancelled=changed)
            return True

    def claim(self, run_id: str, workflow: Workflow, ttl: float) -> Lease | None:
        with self.transaction():
            return self._claim_locked(run_id, workflow, ttl)

    def claim_next(
        self, workflow: Workflow, ttl: float, *, exclude: Sequence[str] = ()
    ) -> Lease | None:
        """Atomically claim the oldest eligible run of one definition."""
        claimed = self.claim_next_any((workflow,), ttl, exclude=exclude)
        return None if claimed is None else claimed[1]

    def configure_queue(
        self, workflow: Workflow, *, max_active: int | None, max_queued: int | None
    ) -> None:
        """Persist admission and active-lease limits for an exact definition."""
        for label, value in (("max_active", max_active), ("max_queued", max_queued)):
            if value is not None and (type(value) is not int or value < 1):
                raise ValueError(f"{label} must be a positive integer or None")
        with self.transaction():
            self.db.execute(
                """INSERT INTO queue_policies(fingerprint,name,max_active,max_queued)
                VALUES(?,?,?,?) ON CONFLICT(fingerprint) DO UPDATE SET
                name=excluded.name,max_active=excluded.max_active,
                max_queued=excluded.max_queued""",
                (workflow.fingerprint, workflow.name, max_active, max_queued),
            )

    def queue_stats(self) -> list[dict[str, Any]]:
        """Point-in-time queue depth, live leases, and oldest eligible wait by definition."""
        now = time.time()
        if self.schema_version < 4:
            rows = self.db.execute(
                "SELECT DISTINCT fingerprint,name,NULL AS max_active,NULL AS max_queued FROM runs"
            ).fetchall()
        else:
            rows = self.db.execute(
                """SELECT fingerprint,name,max_active,max_queued FROM queue_policies
                UNION SELECT fingerprint,name,NULL,NULL FROM runs
                WHERE fingerprint NOT IN (SELECT fingerprint FROM queue_policies)"""
            ).fetchall()
        result = []
        for row in rows:
            counts = self.db.execute(
                """SELECT
                SUM(CASE WHEN status='pending' AND ready_at<=? THEN 1 ELSE 0 END) AS ready,
                SUM(CASE WHEN status='pending' AND ready_at>? THEN 1 ELSE 0 END) AS delayed,
                SUM(CASE WHEN status='paused' THEN 1 ELSE 0 END) AS paused,
                SUM(CASE WHEN status='waiting' THEN 1 ELSE 0 END) AS waiting,
                SUM(CASE WHEN status='running' AND owner IS NOT NULL
                    AND lease_until>? THEN 1 ELSE 0 END) AS active,
                SUM(CASE WHEN status='running' AND lease_until<=? THEN 1 ELSE 0 END) AS recoverable,
                MIN(CASE WHEN status='pending' AND ready_at<=? THEN MAX(created_at,ready_at)
                    WHEN status='paused' THEN updated_at
                    WHEN status='running' AND lease_until<=? THEN lease_until END) AS oldest
                FROM runs WHERE fingerprint=?""",
                (now, now, now, now, now, now, row["fingerprint"]),
            ).fetchone()
            result.append(
                {
                    "fingerprint": row["fingerprint"],
                    "name": row["name"],
                    "max_active": row["max_active"],
                    "max_queued": row["max_queued"],
                    "ready": counts["ready"] or 0,
                    "delayed": counts["delayed"] or 0,
                    "paused": counts["paused"] or 0,
                    "waiting": counts["waiting"] or 0,
                    "active": counts["active"] or 0,
                    "recoverable": counts["recoverable"] or 0,
                    "oldest_ready_age_seconds": None
                    if counts["oldest"] is None
                    else max(0.0, now - counts["oldest"]),
                }
            )
        return sorted(result, key=lambda item: (item["name"], item["fingerprint"]))

    def claim_next_any(
        self, workflows: Sequence[Workflow], ttl: float, *, exclude: Sequence[str] = ()
    ) -> tuple[Workflow, Lease] | None:
        """Claim from the least recently served eligible definition in one transaction.

        A global SQLite sequence gives equal-share round-robin scheduling across
        competing local worker processes, while preserving FIFO within a definition.
        """
        if not workflows:
            raise ValueError("at least one workflow is required")
        if len({workflow.fingerprint for workflow in workflows}) != len(workflows):
            raise ValueError("workflow definitions must be unique")
        excluded = tuple(exclude)
        with self.transaction():
            now = time.time()
            candidates: list[tuple[int, str, Workflow, str]] = []
            for workflow in workflows:
                self.db.execute(
                    """INSERT OR IGNORE INTO queue_policies(fingerprint,name)
                    VALUES(?,?)""",
                    (workflow.fingerprint, workflow.name),
                )
                policy = self.db.execute(
                    "SELECT max_active,last_claimed FROM queue_policies WHERE fingerprint=?",
                    (workflow.fingerprint,),
                ).fetchone()
                if policy["max_active"] is not None:
                    active = self.db.execute(
                        """SELECT COUNT(*) FROM runs WHERE fingerprint=? AND status='running'
                        AND owner IS NOT NULL AND lease_until>?""",
                        (workflow.fingerprint, now),
                    ).fetchone()[0]
                    if active >= policy["max_active"]:
                        continue
                query = """SELECT id FROM runs WHERE fingerprint=?
                    AND status IN ('pending','paused','running')
                    AND (owner IS NULL OR lease_until<=?)
                    AND (status!='pending' OR ready_at<=?)"""
                parameters: list[Any] = [workflow.fingerprint, now, now]
                if excluded:
                    query += f" AND id NOT IN ({','.join('?' for _ in excluded)})"
                    parameters.extend(excluded)
                row = self.db.execute(
                    query + " ORDER BY created_at,id LIMIT 1", parameters
                ).fetchone()
                if row is not None:
                    candidates.append(
                        (policy["last_claimed"], workflow.fingerprint, workflow, row["id"])
                    )
            if not candidates:
                return None
            _, _, workflow, run_id = min(candidates, key=lambda item: (item[0], item[1]))
            lease = self._claim_locked(run_id, workflow, ttl)
            if lease is None:
                return None
            self.db.execute("UPDATE scheduler_clock SET tick=tick+1 WHERE id=1")
            tick = self.db.execute("SELECT tick FROM scheduler_clock WHERE id=1").fetchone()[0]
            self.db.execute(
                "UPDATE queue_policies SET last_claimed=? WHERE fingerprint=?",
                (tick, workflow.fingerprint),
            )
            return workflow, lease

    def _claim_locked(self, run_id: str, workflow: Workflow, ttl: float) -> Lease | None:
        run = self.run(run_id)
        if run["fingerprint"] != workflow.fingerprint:
            raise DefinitionMismatch(
                "workflow changed; restore the original definition or start a new run"
            )
        if run["status"] in ("succeeded", "failed", "cancelled", "waiting"):
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

    def mark_waiting(self, lease: Lease, name: str, signal: str) -> bool:
        """Park an unstarted task unless its signal arrived first."""
        with self.transaction():
            self._fence(lease)
            if self.db.execute(
                "SELECT 1 FROM signals WHERE run_id=? AND name=?", (lease.run_id, signal)
            ).fetchone():
                return False
            changed = self.db.execute(
                """UPDATE tasks SET status='waiting' WHERE run_id=? AND name=?
                AND status='pending'""",
                (lease.run_id, name),
            ).rowcount
            if changed:
                self._event(lease.run_id, "task.waiting", name, signal=signal)
            return bool(changed)

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
        if status not in ("succeeded", "failed", "paused", "waiting"):
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
            if status == "waiting":
                # A signal can arrive between scheduling and lease release.
                manifest = self.run(lease.run_id)["manifest"]
                ready = {
                    t["name"]
                    for t in manifest["tasks"]
                    if t.get("wait_for")
                    and self.db.execute(
                        "SELECT 1 FROM signals WHERE run_id=? AND name=?",
                        (lease.run_id, t["wait_for"]),
                    ).fetchone()
                }
                woke = 0
                for name in ready:
                    woke += self.db.execute(
                        """UPDATE tasks SET status='pending'
                        WHERE run_id=? AND name=? AND status='waiting'""",
                        (lease.run_id, name),
                    ).rowcount
                states = self.tasks(lease.run_id)
                runnable_signal = any(
                    t["name"] in ready
                    and states[t["name"]]["status"] == "pending"
                    and all(states[dep]["status"] == "succeeded" for dep in t["needs"])
                    for t in manifest["tasks"]
                )
                if woke or runnable_signal:
                    status = "pending"
            self.db.execute(
                "UPDATE runs SET status=?,owner=NULL,lease_until=0,updated_at=? WHERE id=?",
                (status, now, lease.run_id),
            )
            self._event(lease.run_id, f"run.{status}")
