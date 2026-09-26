from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path

from retrace import Store, Task, Workflow
from retrace.store import _SCHEMA


async def identity(ctx):
    return ctx.input


class SubmissionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name, "runs.db")
        self.workflow = Workflow("submission", (Task("identity", identity),))

    def tearDown(self):
        self.directory.cleanup()

    def test_repeated_key_returns_original_run_without_new_events(self):
        with Store(self.path) as store:
            run_id = store.create(self.workflow, {"value": 1}, key="order-42", ready_at=120)
            self.assertEqual(
                store.create(self.workflow, {"value": 1}, key="order-42", ready_at=999),
                run_id,
            )
            self.assertEqual(len(store.runs()), 1)
            self.assertEqual(len(store.events(run_id)), 1)
            self.assertEqual(store.run(run_id)["ready_at"], 120)
            self.assertEqual(
                store.run(run_id)["submission_key_hash"],
                hashlib.sha256(b"order-42").hexdigest(),
            )
            self.assertNotIn("order-42", json.dumps(store.run(run_id)))
            with self.assertRaisesRegex(ValueError, "different work"):
                store.create(self.workflow, {"value": 2}, key="order-42")
            changed = Workflow("changed", (Task("identity", identity),))
            with self.assertRaisesRegex(ValueError, "different work"):
                store.create(changed, {"value": 1}, key="order-42")
            with self.assertRaisesRegex(ValueError, "another run ID"):
                store.create(self.workflow, {"value": 1}, key="order-42", run_id="other")

    def test_future_run_is_not_dispatched_but_manual_resume_can_override(self):
        with Store(self.path) as store:
            future = store.create(self.workflow, 1, ready_at=time.time() + 60)
            immediate = store.create(self.workflow, 2)
            self.assertEqual(store.claim_next(self.workflow, 15).run_id, immediate)
            self.assertIsNone(store.claim_next(self.workflow, 15))
            lease = store.claim(future, self.workflow, 15)
            self.assertEqual(lease.run_id, future)
            self.assertEqual(store.run(future)["ready_at"], 0)

    def test_submission_validation(self):
        with Store(self.path) as store:
            for key in ("", "x" * 129, 7):
                with self.assertRaisesRegex(ValueError, "key"):
                    store.create(self.workflow, key=key)
            for ready_at in (-1, float("inf"), float("nan"), True, "tomorrow"):
                with self.assertRaisesRegex(ValueError, "ready_at"):
                    store.create(self.workflow, ready_at=ready_at)
            with self.assertRaisesRegex(ValueError, "run_id"):
                store.create(self.workflow, run_id="")

    def test_version_one_database_migrates_without_losing_runs(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executescript(_SCHEMA)
            db.execute("PRAGMA user_version=1")
            db.execute(
                """INSERT INTO runs(id,name,version,fingerprint,manifest,input,status,
                created_at,updated_at) VALUES(?,?,?,?,?,?,'pending',?,?)""",
                ("legacy", "old", "1", "legacy-fingerprint", "{}", "null", 1, 1),
            )
        with Store(self.path) as store:
            self.assertEqual(store.db.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertEqual(store.run("legacy")["status"], "pending")
            self.assertEqual(store.run("legacy")["ready_at"], 0)
            self.assertIsNone(store.run("legacy")["submission_key_hash"])
            self.assertEqual(store.create(self.workflow, 3, key="new"), store.runs()[0]["id"])
        with Store(self.path, readonly=True) as store:
            self.assertEqual(store.run("legacy")["status"], "pending")


class SubmissionProcessTests(unittest.TestCase):
    def test_competing_producers_get_one_run_for_same_key(self):
        with tempfile.TemporaryDirectory() as directory:
            db = str(Path(directory, "runs.db"))
            command = [
                sys.executable,
                "-m",
                "retrace",
                "--db",
                db,
                "submit",
                "examples.pipeline:workflow",
                "--input",
                '{"values":[2,4]}',
                "--key",
                "batch-42",
            ]
            children = [
                subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                for _ in range(2)
            ]
            try:
                outputs = []
                for child in children:
                    out, err = child.communicate(timeout=20)
                    self.assertEqual(child.returncode, 0, err)
                    outputs.append(json.loads(out))
                self.assertEqual(outputs[0]["run_id"], outputs[1]["run_id"])
                with Store(db) as store:
                    self.assertEqual(len(store.runs()), 1)
                    self.assertEqual(len(store.events(outputs[0]["run_id"])), 1)
            finally:
                for child in children:
                    if child.poll() is None:
                        child.kill()
                    child.communicate(timeout=5)
