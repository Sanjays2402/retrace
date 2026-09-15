from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from retrace import (
    Context,
    DefinitionMismatch,
    Engine,
    LeaseLost,
    RetryPolicy,
    RunBusy,
    Store,
    Task,
    Workflow,
)
from retrace.workflow import encode


async def value(ctx):
    return ctx.input


class EngineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "test.db"
        self.store = Store(self.path)
        self.engine = Engine(self.store)

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    async def test_dependency_outputs_and_completed_resume(self):
        calls = []

        async def first(ctx):
            calls.append(ctx.task_name)
            return {"n": ctx.input["n"] + 1}

        async def last(ctx):
            calls.append(ctx.task_name)
            return ctx.dependencies["first"]["n"] * 2

        workflow = Workflow("graph", (Task("first", first), Task("last", last, needs=("first",))))
        result = await self.engine.run(workflow, {"n": 20})
        self.assertEqual(result.outputs["last"], 42)
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(await self.engine.resume(workflow, result.run_id), result)
        self.assertEqual(calls, ["first", "last"])
        self.assertEqual(self.store.events(result.run_id)[-1]["kind"], "run.succeeded")

    async def test_parallel_execution_respects_bound(self):
        active = peak = 0

        async def work(ctx):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.03)
            active -= 1
            return ctx.task_name

        engine = Engine(self.store, concurrency=2)
        result = await engine.run(
            Workflow("parallel", tuple(Task(f"task{i}", work) for i in range(6)))
        )
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(peak, 2)

    async def test_retry_persists_attempts_and_stable_key(self):
        keys = []

        async def flaky(ctx):
            keys.append(ctx.idempotency_key)
            if ctx.attempt < 3:
                raise ConnectionError("try again")
            return "ok"

        result = await self.engine.run(
            Workflow("retry", (Task("flaky", flaky, retry=RetryPolicy(3, 0, 0)),))
        )
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(len(set(keys)), 1)
        self.assertEqual(
            [a["status"] for a in self.store.history(result.run_id)],
            ["failed", "failed", "succeeded"],
        )
        self.assertEqual(self.store.tasks(result.run_id)["flaky"]["failures"], 2)

    async def test_failure_blocks_descendants_but_independent_work_finishes(self):
        async def fail(ctx):
            raise RuntimeError("expected")

        workflow = Workflow(
            "failure",
            (
                Task("a", fail, retry=RetryPolicy(1)),
                Task("c", value, needs=("b",)),
                Task("b", value, needs=("a",)),
                Task("independent", value),
            ),
        )
        result = await self.engine.run(workflow, 42)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.outputs, {"independent": 42})
        self.assertIn("expected", result.errors["a"])
        states = self.store.tasks(result.run_id)
        self.assertEqual(states["b"]["status"], "blocked")
        self.assertEqual(states["c"]["status"], "blocked")
        self.assertEqual(states["b"]["attempts"], 0)

    async def test_timeout_is_recorded(self):
        async def slow(ctx):
            await asyncio.sleep(10)

        result = await self.engine.run(
            Workflow("timeout", (Task("slow", slow, timeout=0.01, retry=RetryPolicy(1)),))
        )
        self.assertEqual(result.status, "failed")
        self.assertIn("TimeoutError", result.errors["slow"])

    async def test_cancel_then_resume_reuses_checkpoint(self):
        entered = asyncio.Event()
        calls = []
        pause = True

        async def checkpoint(ctx):
            calls.append("checkpoint")
            return 7

        async def finish(ctx):
            entered.set()
            if pause:
                await asyncio.sleep(10)
            return ctx.dependencies["checkpoint"] + 1

        workflow = Workflow(
            "resume",
            (Task("checkpoint", checkpoint), Task("finish", finish, needs=("checkpoint",))),
        )
        run_id = self.store.create(workflow)
        future = asyncio.create_task(self.engine.resume(workflow, run_id))
        try:
            # Readiness is a condition, not a filesystem latency benchmark.
            await asyncio.wait_for(entered.wait(), 30)
            future.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await future
        finally:
            # Also clean up on assertion/timeout failures before tearDown closes SQLite.
            future.cancel()
            await asyncio.gather(future, return_exceptions=True)
        self.assertEqual(self.store.run(run_id)["status"], "paused")
        pause = False
        result = await self.engine.resume(workflow, run_id)
        self.assertEqual(result.outputs["finish"], 8)
        self.assertEqual(calls, ["checkpoint"])
        self.assertEqual(self.store.tasks(run_id)["finish"]["failures"], 0)

    async def test_heartbeat_keeps_run_owned(self):
        entered = asyncio.Event()

        async def slow(ctx):
            entered.set()
            await asyncio.sleep(4)
            return 1

        workflow = Workflow("heartbeat", (Task("slow", slow),))
        run_id = self.store.create(workflow)
        future = asyncio.create_task(Engine(self.store, lease_ttl=3).resume(workflow, run_id))
        await entered.wait()
        # Cross the original lease deadline to prove renewal, allowing slow CI disk flushes.
        await asyncio.sleep(3.25)
        with Store(self.path) as other, self.assertRaises(RunBusy):
            other.claim(run_id, workflow, 1)
        self.assertEqual((await future).status, "succeeded")

    async def test_non_json_output_is_task_failure(self):
        async def bad(ctx):
            return object()

        result = await self.engine.run(Workflow("json", (Task("bad", bad, retry=RetryPolicy(1)),)))
        self.assertEqual(result.status, "failed")
        self.assertIn("TypeError", result.errors["bad"])

    async def test_resume_rejects_changed_definition(self):
        workflow = Workflow("versioned", (Task("a", value),))
        run_id = self.store.create(workflow)
        with self.assertRaises(DefinitionMismatch):
            await self.engine.resume(Workflow("versioned", workflow.tasks, version="2"), run_id)

    async def test_input_mutation_does_not_leak(self):
        async def mutate(ctx):
            ctx.input["n"] = 999

        workflow = Workflow(
            "isolation", (Task("mutate", mutate), Task("read", value, needs=("mutate",)))
        )
        result = await self.engine.run(workflow, {"n": 1})
        self.assertEqual(result.outputs["read"], {"n": 1})

    async def test_invalid_engine_settings(self):
        for concurrency in (0, -1, True, 1.5):
            with self.assertRaises(ValueError):
                Engine(self.store, concurrency=concurrency)
        for ttl in (0, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                Engine(self.store, lease_ttl=ttl)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "test.db"
        self.store = Store(self.path)
        self.workflow = Workflow("test", (Task("step", value),))
        self.run_id = self.store.create(self.workflow)

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def test_exclusive_ownership_and_stale_worker_fencing(self):
        old = self.store.claim(self.run_id, self.workflow, 5)
        self.store.start_task(old, "step")
        with Store(self.path) as other:
            with self.assertRaises(RunBusy):
                other.claim(self.run_id, self.workflow, 5)
            self.store.db.execute(
                "UPDATE runs SET lease_until=? WHERE id=?", (time.time() - 1, self.run_id)
            )
            new = other.claim(self.run_id, self.workflow, 5)
            self.assertGreater(new.epoch, old.epoch)
            with self.assertRaises(LeaseLost):
                self.store.finish_task(old, "step", output="stale")
            with self.assertRaises(LeaseLost):
                self.store.heartbeat(old, 5)
            self.assertEqual(other.history(self.run_id)[0]["status"], "interrupted")
            other.start_task(new, "step")
            other.finish_task(new, "step", output="fresh")
            other.release(new, "succeeded")
        self.assertEqual(self.store.tasks(self.run_id)["step"]["output"], "fresh")

    def test_checkpoint_and_event_rollback_together(self):
        lease = self.store.claim(self.run_id, self.workflow, 5)
        self.store.start_task(lease, "step")
        original = self.store._event

        def fail(*args, **kwargs):
            raise sqlite3.OperationalError("injected event write failure")

        self.store._event = fail
        with self.assertRaises(sqlite3.OperationalError):
            self.store.finish_task(lease, "step", output=42)
        self.store._event = original
        self.assertEqual(self.store.tasks(self.run_id)["step"]["status"], "running")
        self.assertEqual(self.store.history(self.run_id)[0]["status"], "running")
        self.assertNotIn("task.succeeded", [e["kind"] for e in self.store.events(self.run_id)])

    def test_cursor_pagination_and_readonly_connection(self):
        lease = self.store.claim(self.run_id, self.workflow, 5)
        self.store.start_task(lease, "step")
        self.store.finish_task(lease, "step", output=42)
        first = self.store.events(self.run_id, limit=2)
        second = self.store.events(self.run_id, after=first[-1]["id"])
        self.assertEqual(len(first + second), 4)
        with Store(self.path, readonly=True) as readonly:
            self.assertEqual(readonly.tasks(self.run_id)["step"]["output"], 42)
            with self.assertRaises(sqlite3.OperationalError):
                readonly.create(self.workflow)

    def test_unknown_run_duplicate_id_and_future_schema(self):
        with self.assertRaises(KeyError):
            self.store.run("missing")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.create(self.workflow, run_id=self.run_id)
        self.assertEqual(len(self.store.runs()), 1)
        self.store.db.execute("PRAGMA user_version=99")
        with self.assertRaises(ValueError):
            Store(self.path)


class WorkflowTests(unittest.TestCase):
    def test_invalid_graphs(self):
        for tasks in (
            (),
            (Task("a", value), Task("a", value)),
            (Task("a", value, needs=("missing",)),),
            (Task("a", value, needs=("b",)), Task("b", value, needs=("a",))),
            (Task("a", value, needs=("a",)),),
        ):
            with self.subTest(tasks=tasks), self.assertRaises(ValueError):
                Workflow("test", tasks)

    def test_task_and_retry_validation(self):
        with self.assertRaises(TypeError):
            Task("sync", lambda ctx: 1)
        for name in ("", "has space", "<script>"):
            with self.assertRaises(ValueError):
                Task(name, value)
        with self.assertRaises(ValueError):
            Task("x", value, needs=("a", "a"))
        for timeout in (0, -1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                Task("x", value, timeout=timeout)
        for kwargs in (
            {"max_attempts": 0},
            {"max_attempts": True},
            {"initial_delay": -1},
            {"max_delay": float("inf")},
            {"initial_delay": 10, "max_delay": 1},
        ):
            with self.assertRaises(ValueError):
                RetryPolicy(**kwargs)

    def test_fingerprint_order_and_version(self):
        tasks = (Task("a", value), Task("b", value, needs=("a",)))
        a = Workflow("test", tasks)
        self.assertEqual(a.fingerprint, Workflow("test", tuple(reversed(tasks))).fingerprint)
        self.assertNotEqual(a.fingerprint, Workflow("test", tasks, version="2").fingerprint)

    def test_backoff_cap(self):
        retry = RetryPolicy(initial_delay=1, max_delay=5)
        self.assertEqual([retry.delay(i) for i in (1, 2, 3, 4, 10000)], [1, 2, 4, 5, 5])

    def test_json_limits(self):
        for data in (float("nan"), float("inf"), "x" * 1_048_576):
            with self.assertRaises(ValueError):
                encode(data)

    def test_idempotency_scope(self):
        key = Context("run", "task", 1, None, {}).idempotency_key
        self.assertEqual(key, Context("run", "task", 99, None, {}).idempotency_key)
        self.assertNotEqual(key, Context("other", "task", 1, None, {}).idempotency_key)
        self.assertNotEqual(key, Context("run", "other", 1, None, {}).idempotency_key)
