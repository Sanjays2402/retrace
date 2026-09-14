from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from retrace import DefinitionMismatch, Engine, RetryPolicy, RunBusy, Store, Task, Workflow


class RetryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name, "runs.db")
        self.store = Store(self.path)
        self.engine = Engine(self.store)
        self.broken = {"left", "right"}
        self.calls = {}
        self.keys = {}

        async def work(ctx):
            self.calls[ctx.task_name] = self.calls.get(ctx.task_name, 0) + 1
            self.keys.setdefault(ctx.task_name, set()).add(ctx.idempotency_key)
            if ctx.task_name in self.broken:
                raise ConnectionError(f"{ctx.task_name} unavailable")
            return 1 + sum(ctx.dependencies.values())

        # Deliberately use reverse dependency order to exercise topological planning.
        graph = {
            "tail": ("join",),
            "join": ("left_child", "right"),
            "left_child": ("left",),
            "right": ("root",),
            "left": ("root",),
            "independent": (),
            "root": (),
        }
        self.workflow = Workflow(
            "selective",
            tuple(
                Task(name, work, needs=needs, retry=RetryPolicy(2, 0, 0))
                for name, needs in graph.items()
            ),
        )

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    async def failed_run(self):
        result = await self.engine.run(self.workflow)
        self.assertEqual(result.status, "failed")
        return result.run_id

    async def test_default_retry_preserves_successes_history_and_keys(self):
        run_id = await self.failed_run()
        history = self.store.history(run_id)
        checkpoints = self.store.tasks(run_id)
        self.broken.clear()
        result = await self.engine.retry(self.workflow, run_id)
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.run_id, run_id)
        self.assertEqual(self.calls["root"], 1)
        self.assertEqual(self.calls["independent"], 1)
        self.assertEqual(self.calls["left"], 3)
        self.assertTrue(all(len(keys) == 1 for keys in self.keys.values()))
        self.assertEqual(self.store.history(run_id)[: len(history)], history)
        self.assertEqual(self.store.tasks(run_id)["root"], checkpoints["root"])
        self.assertEqual(self.store.tasks(run_id)["left"]["failures"], 0)
        events = self.store.events(run_id)
        reset = next(e for e in events if e["kind"] == "task.reset" and e["task_name"] == "left")
        self.assertEqual(reset["payload"]["previous_failures"], 2)
        self.assertEqual(sum(e["kind"] == "run.retry_requested" for e in events), 1)

    async def test_partial_retry_keeps_shared_fanin_blocked_until_both_sides_recover(self):
        run_id = await self.failed_run()
        plan = self.store.retry_plan(self.workflow, run_id, ["left"])
        self.assertEqual(plan.selected, ("left",))
        self.assertEqual(plan.reset, ("left", "left_child"))
        self.assertEqual(plan.preserved, ("independent", "root"))
        self.assertEqual(plan.remaining_failed, ("right",))
        self.assertEqual(plan.remaining_blocked, ("join", "tail"))
        self.broken.remove("left")
        result = await self.engine.retry(self.workflow, run_id, tasks=["left"])
        self.assertEqual(result.status, "failed")
        self.assertEqual(self.calls["right"], 2)
        self.assertEqual(self.calls["left_child"], 1)
        self.assertNotIn("join", self.calls)
        self.broken.clear()
        result = await self.engine.retry(self.workflow, run_id, tasks=["right"])
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(self.calls["left"], 3)
        self.assertEqual(self.calls["left_child"], 1)
        self.assertEqual(result.outputs["tail"], 7)

    async def test_preview_is_readonly_and_does_not_consume_retry_budget(self):
        run_id = await self.failed_run()
        before = (self.store.run(run_id), self.store.tasks(run_id), self.store.events(run_id))
        with Store(self.path, readonly=True) as reader:
            plan = reader.retry_plan(self.workflow, run_id)
        self.assertEqual(plan.selected, ("left", "right"))
        self.assertEqual(
            before, (self.store.run(run_id), self.store.tasks(run_id), self.store.events(run_id))
        )

    async def test_failed_manual_retry_gets_a_fresh_budget_without_renumbering_attempts(self):
        run_id = await self.failed_run()
        result = await self.engine.retry(self.workflow, run_id, tasks=["left"])
        self.assertEqual(result.status, "failed")
        self.assertEqual(self.store.tasks(run_id)["left"]["attempts"], 4)
        self.assertEqual(self.store.tasks(run_id)["left"]["failures"], 2)
        self.assertEqual(
            [a["number"] for a in self.store.history(run_id) if a["task_name"] == "left"],
            [1, 2, 3, 4],
        )

    async def test_retry_validation_rejects_nonfailed_selections_and_changed_code_contract(self):
        run_id = await self.failed_run()
        for selected in ([], ["missing"], ["root"], ["join"], ["left", "left"], "left"):
            with self.subTest(selected=selected), self.assertRaises(ValueError):
                self.store.retry_failed(self.workflow, run_id, selected)
        with self.assertRaises(DefinitionMismatch):
            self.store.retry_failed(Workflow("selective", self.workflow.tasks, version="2"), run_id)
        with self.assertRaises(KeyError):
            self.store.retry_failed(self.workflow, "missing")
        self.assertEqual(self.store.run(run_id)["status"], "failed")

    async def test_pending_succeeded_paused_and_live_runs_cannot_be_retried(self):
        run_id = self.store.create(self.workflow)
        with self.assertRaises(ValueError):
            self.store.retry_failed(self.workflow, run_id)
        lease = self.store.claim(run_id, self.workflow, 5)
        with self.assertRaises(RunBusy):
            self.store.retry_failed(self.workflow, run_id)
        self.store.release(lease, "paused")
        with self.assertRaises(ValueError):
            self.store.retry_failed(self.workflow, run_id)
        self.broken.clear()
        await self.engine.resume(self.workflow, run_id)
        with self.assertRaises(ValueError):
            self.store.retry_failed(self.workflow, run_id)

    async def test_reset_and_journal_roll_back_as_one_transaction(self):
        run_id = await self.failed_run()
        before = (
            self.store.run(run_id),
            self.store.tasks(run_id),
            self.store.events(run_id),
            self.store.history(run_id),
        )
        original = self.store._event

        def inject(run_id, kind, *args, **kwargs):
            if kind == "run.retry_requested":
                raise sqlite3.OperationalError("injected retry journal failure")
            return original(run_id, kind, *args, **kwargs)

        with (
            patch.object(self.store, "_event", side_effect=inject),
            self.assertRaises(sqlite3.OperationalError),
        ):
            self.store.retry_failed(self.workflow, run_id)
        self.assertEqual(
            before,
            (
                self.store.run(run_id),
                self.store.tasks(run_id),
                self.store.events(run_id),
                self.store.history(run_id),
            ),
        )

    async def test_reopened_run_survives_process_gap_before_resume(self):
        run_id = await self.failed_run()
        self.store.retry_failed(self.workflow, run_id)
        self.store.close()
        self.store = Store(self.path)
        self.broken.clear()
        result = await Engine(self.store).resume(self.workflow, run_id)
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(self.calls["root"], 1)
        self.assertEqual(self.store.run(run_id)["epoch"], 2)

    async def test_two_connections_cannot_reset_the_same_failure_twice(self):
        run_id = await self.failed_run()
        barrier = threading.Barrier(2)

        def reset():
            with Store(self.path) as store:
                barrier.wait(timeout=5)
                try:
                    store.retry_failed(self.workflow, run_id)
                    return "reset"
                except ValueError:
                    return "rejected"

        results = await asyncio.gather(asyncio.to_thread(reset), asyncio.to_thread(reset))
        self.assertEqual(sorted(results), ["rejected", "reset"])
        self.assertEqual(
            sum(e["kind"] == "run.retry_requested" for e in self.store.events(run_id)), 1
        )
