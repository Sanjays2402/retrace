from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from retrace import Engine, RetryPolicy, Store, Task, Workflow


async def value(ctx):
    return ctx.signal if ctx.signal is not None else ctx.input


async def fail(ctx):
    raise ValueError("repairable failure")


class PruneTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name, "runs.db")
        self.store = Store(self.path)
        self.workflow = Workflow("cleanup", (Task("value", value),))

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def age(self, run_id, at=10):
        self.store.db.execute("UPDATE runs SET updated_at=? WHERE id=?", (at, run_id))

    async def complete(self, run_id, *, key=None):
        self.store.create(self.workflow, 42, run_id=run_id, key=key)
        await Engine(self.store).resume(self.workflow, run_id)
        self.age(run_id)
        return run_id

    async def test_preview_is_readonly_and_removal_includes_all_dependent_records(self):
        workflow = Workflow("signaled", (Task("value", value, wait_for="approval"),))
        run_id = self.store.create(workflow)
        self.store.signal(run_id, "approval", {"approved": True})
        await Engine(self.store).resume(workflow, run_id)
        self.age(run_id)
        before = tuple(self.store.db.iterdump())
        with Store(self.path, readonly=True) as reader:
            plan = reader.prune_plan(100)
        self.assertEqual(tuple(self.store.db.iterdump()), before)
        self.assertEqual(plan.run_ids, (run_id,))
        self.assertEqual((plan.tasks, plan.attempts, plan.signals), (1, 1, 1))
        self.assertEqual(plan.events, len(self.store.events(run_id)))
        receipt = self.store.prune(100)
        self.assertEqual(plan, receipt)
        for table in ("runs", "tasks", "attempts", "events", "signals"):
            self.assertEqual(
                self.store.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0
            )
        self.assertEqual(self.store.db.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(self.store.db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        with self.assertRaises(KeyError):
            self.store.run(run_id)

    async def test_unfinished_recent_failed_and_keyed_runs_are_protected(self):
        deleted = await self.complete("old-success")
        cancelled = self.store.create(self.workflow, run_id="old-cancelled")
        self.store.cancel(cancelled)
        self.age(cancelled)
        protected = [await self.complete("keyed", key="payment-42")]
        protected.append(await self.complete("recent"))
        self.age("recent", 100)
        failed_workflow = Workflow("failed", (Task("fail", fail, retry=RetryPolicy(1)),))
        failed = await Engine(self.store).run(failed_workflow)
        protected.append(failed.run_id)
        for status in ("pending", "paused", "running", "waiting"):
            run_id = self.store.create(self.workflow, run_id=f"keep-{status}")
            if status == "paused":
                lease = self.store.claim(run_id, self.workflow, 5)
                self.store.release(lease, "paused")
            elif status == "running":
                self.store.claim(run_id, self.workflow, 5)
            elif status == "waiting":
                waiting = Workflow("waiting", (Task("value", value, wait_for="approval"),))
                run_id = (await Engine(self.store).run(waiting)).run_id
            protected.append(run_id)
        for run_id in protected:
            if run_id != "recent":
                self.age(run_id)
        plan = self.store.prune(100)
        self.assertEqual(set(plan.run_ids), {deleted, cancelled})
        self.assertEqual(plan.protected_keyed_runs, 1)
        for run_id in protected:
            self.store.run(run_id)
        self.assertEqual(self.store.create(self.workflow, 42, key="payment-42"), "keyed")
        with self.assertRaises(ValueError):
            self.store.create(self.workflow, 99, key="payment-42")
        self.assertEqual(self.store.prune(100, include_failed=True).run_ids, (failed.run_id,))

    async def test_batches_are_deterministic_and_retained_event_cursors_do_not_rewind(self):
        retained = await self.complete("retained")
        self.age(retained, 200)
        retained_cursor = self.store.events(retained)[-1]["id"]
        for name in ("c", "a", "b"):
            await self.complete(name)
        cursor = self.store.events("b")[-1]["id"]
        self.assertEqual(self.store.prune(100, limit=2).run_ids, ("a", "b"))
        self.assertEqual(self.store.prune(100, limit=2).run_ids, ("c",))
        self.assertEqual(self.store.prune(100).run_ids, ())
        self.assertEqual(self.store.events(retained)[-1]["id"], retained_cursor)
        new = await self.complete("new")
        self.assertGreater(self.store.events(new)[0]["id"], cursor)

    async def test_apply_reselects_after_a_failed_run_has_been_reopened(self):
        workflow = Workflow("repair", (Task("fail", fail, retry=RetryPolicy(1)),))
        result = await Engine(self.store).run(workflow)
        self.age(result.run_id)
        self.assertEqual(self.store.prune_plan(100, include_failed=True).run_ids, (result.run_id,))
        self.store.retry_failed(workflow, result.run_id)
        self.age(result.run_id)
        self.assertEqual(self.store.prune(100, include_failed=True).run_ids, ())
        self.assertEqual(self.store.run(result.run_id)["status"], "pending")

    async def test_all_deletions_roll_back_on_a_write_failure(self):
        await self.complete("rollback")
        self.store.db.execute("""CREATE TRIGGER reject_prune BEFORE DELETE ON runs
            BEGIN SELECT RAISE(ABORT, 'injected delete failure'); END""")
        before = tuple(self.store.db.iterdump())
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.prune(100)
        self.assertEqual(tuple(self.store.db.iterdump()), before)

    async def test_competing_cleanup_connections_delete_each_run_once(self):
        await self.complete("contended")
        barrier = threading.Barrier(2)

        def prune():
            with Store(self.path) as store:
                barrier.wait(timeout=5)
                return store.prune(100).run_ids

        results = await asyncio.gather(asyncio.to_thread(prune), asyncio.to_thread(prune))
        self.assertEqual(sorted(results), [(), ("contended",)])

    def test_validation_and_readonly_legacy_database(self):
        for before in (True, -1, float("nan"), float("inf"), "100"):
            with self.subTest(before=before), self.assertRaises(ValueError):
                self.store.prune_plan(before)
        for limit in (True, 0, 501, 1.5):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                self.store.prune(100, limit=limit)
        with self.assertRaises(ValueError):
            self.store.prune_plan(100, include_failed=1)
        self.store.db.execute("PRAGMA user_version=3")
        with (
            Store(self.path, readonly=True) as reader,
            self.assertRaisesRegex(ValueError, "migrate"),
        ):
            reader.prune_plan(100)
