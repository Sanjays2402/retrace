from __future__ import annotations

import asyncio
import json
import random
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from retrace import Engine, LeaseLost, RetryPolicy, Store, Task, Workflow


class ProcessRecoveryTests(unittest.TestCase):
    def test_killed_worker_recovers_in_a_new_process(self):
        with tempfile.TemporaryDirectory() as directory:
            db = str(Path(directory, "runs.db"))
            command = [sys.executable, "-m", "retrace", "--db", db, "--lease-ttl", "0.3"]
            child = subprocess.Popen(
                command
                + [
                    "run",
                    "tests.fixtures.crash:workflow",
                    "--input",
                    json.dumps({"directory": directory}),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                deadline = time.monotonic() + 10
                while not Path(directory, "started").exists():
                    if child.poll() is not None or time.monotonic() > deadline:
                        self.fail(f"worker did not reach kill point: {child.poll()}")
                    time.sleep(0.01)
                child.kill()  # SIGKILL on POSIX; no finally blocks or graceful cleanup.
                child.communicate(timeout=5)
                with Store(db) as store:
                    run_id = store.runs()[0]["id"]
                    self.assertEqual(store.tasks(run_id)["checkpoint"]["status"], "succeeded")
                    self.assertEqual(store.tasks(run_id)["finish"]["status"], "running")
                time.sleep(0.4)
                resumed = subprocess.run(
                    command + ["resume", "tests.fixtures.crash:workflow", run_id],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                self.assertEqual(resumed.returncode, 0, resumed.stderr)
                self.assertEqual(json.loads(resumed.stdout)["outputs"]["finish"], 42)
                self.assertEqual(Path(directory, "checkpoint-calls").read_text(), "called\n")
                with Store(db) as store:
                    self.assertEqual(store.run(run_id)["epoch"], 2)
                    self.assertEqual(
                        [a["status"] for a in store.history(run_id)],
                        ["succeeded", "interrupted", "succeeded"],
                    )
                    self.assertEqual(store.db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            finally:
                if child.poll() is None:
                    child.kill()
                child.communicate(timeout=5)


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_lost_lease_cancels_running_work_and_rejects_result(self):
        entered, cancelled = asyncio.Event(), asyncio.Event()

        async def work(ctx):
            entered.set()
            try:
                await asyncio.sleep(10)
            finally:
                cancelled.set()

        with Store(":memory:") as store:
            workflow = Workflow("lease", (Task("work", work),))
            run_id = store.create(workflow)
            future = asyncio.create_task(Engine(store, lease_ttl=0.3).resume(workflow, run_id))
            await entered.wait()
            store.db.execute("UPDATE runs SET owner='replacement' WHERE id=?", (run_id,))
            with self.assertRaises(LeaseLost):
                await future
            self.assertTrue(cancelled.is_set())
            self.assertIsNone(store.tasks(run_id)["work"]["output"])

    async def test_retry_deadline_survives_connection_restart(self):
        called = []

        async def work(ctx):
            called.append(time.time())
            return ctx.attempt

        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory, "retry.db")
            workflow = Workflow("deadline", (Task("work", work, retry=RetryPolicy(3, 0.2, 1)),))
            with Store(db) as store:
                run_id = store.create(workflow)
                lease = store.claim(run_id, workflow, 1)
                store.start_task(lease, "work")
                retry_at = time.time() + 0.2
                store.finish_task(lease, "work", error="temporary", retry_at=retry_at)
                store.release(lease, "paused")
            with Store(db) as reopened:
                result = await Engine(reopened).resume(workflow, run_id)
                self.assertEqual(result.outputs["work"], 2)
                self.assertGreaterEqual(called[0], retry_at)
                self.assertEqual(reopened.tasks(run_id)["work"]["failures"], 1)

    async def test_random_dags_match_reference_evaluator(self):
        # Fixed seeds make failures reproducible while exercising many graph shapes.
        for seed in range(25):
            rng = random.Random(seed)
            completed = set()
            tasks, expected = [], {}

            async def work(ctx, completed=completed):
                self.assertTrue(set(ctx.dependencies) <= completed)
                await asyncio.sleep(0)
                completed.add(ctx.task_name)
                return 1 + sum(ctx.dependencies.values())

            for index in range(rng.randint(3, 18)):
                name = f"n{index}"
                needs = tuple(t.name for t in tasks if rng.random() < 0.3)
                tasks.append(Task(name, work, needs=needs))
                expected[name] = 1 + sum(expected[n] for n in needs)
            with self.subTest(seed=seed), Store(":memory:") as store:
                result = await Engine(store, concurrency=3).run(Workflow("random", tuple(tasks)))
                self.assertEqual(result.outputs, expected)
