from __future__ import annotations

import asyncio
import json
import math
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from retrace import LeaseLost, Store, Task, Worker, Workflow


async def identity(ctx):
    return ctx.input


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_claim_next_filters_definition_and_fences_old_owner(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            Store(Path(directory, "queue.db")) as store,
        ):
            first = Workflow("first", (Task("identity", identity),))
            other = Workflow("other", (Task("identity", identity),))
            other_id = store.create(other, "untouched")
            run_id = store.create(first, 42)
            old = store.claim_next(first, 0.3)
            self.assertEqual(old.run_id, run_id)
            self.assertIsNone(store.claim_next(first, 0.3))
            store.db.execute("UPDATE runs SET lease_until=0 WHERE id=?", (run_id,))
            new = store.claim_next(first, 1)
            self.assertEqual(new.epoch, 2)
            with self.assertRaises(LeaseLost):
                store.heartbeat(old, 1)
            self.assertEqual(store.run(other_id)["status"], "pending")
            result = await Worker(store, first).engine._run_claimed(first, new)
            self.assertEqual(result.outputs, {"identity": 42})
            self.assertIsNone(store.claim_next(first, 1))

    async def test_bounded_parallel_runs(self):
        active = peak = 0

        async def work(ctx):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.06)
            active -= 1
            return ctx.input

        workflow = Workflow("pool", (Task("work", work),))
        with Store(":memory:") as store:
            run_ids = {store.create(workflow, n) for n in range(3)}
            worker = Worker(store, workflow, max_runs=2, lease_ttl=120, poll_interval=0.01)
            results = await worker.serve(once=True)
            self.assertEqual({result.run_id for result in results}, run_ids)
            self.assertTrue(all(result.status == "succeeded" for result in results))
            self.assertEqual(peak, 2)
            self.assertEqual(len(await worker.serve(once=True)), 0)

    async def test_invalid_worker_settings(self):
        workflow = Workflow("settings", (Task("identity", identity),))
        with Store(":memory:") as store:
            for kwargs in ({"max_runs": 0}, {"max_runs": True}, {"poll_interval": 0}):
                with self.assertRaises(ValueError):
                    Worker(store, workflow, **kwargs)

    async def test_drain_finishes_owned_run_without_claiming_another(self):
        started = asyncio.Event()
        finish = asyncio.Event()

        async def work(ctx):
            started.set()
            await finish.wait()
            return ctx.input

        workflow = Workflow("drain", (Task("work", work),))
        with Store(":memory:") as store:
            first = store.create(workflow, 1)
            second = store.create(workflow, 2)
            stop = asyncio.Event()
            worker = Worker(store, workflow, poll_interval=0.01)
            serving = asyncio.create_task(worker.serve(once=True, stop_event=stop, drain_timeout=1))
            await asyncio.wait_for(started.wait(), 1)
            stop.set()
            await asyncio.sleep(0.02)
            finish.set()
            results = await asyncio.wait_for(serving, 1)
            self.assertEqual(len(results), 1)
            self.assertIn(results[0].run_id, (first, second))
            self.assertEqual(results[0].status, "succeeded")
            self.assertEqual(
                sorted((store.run(first)["status"], store.run(second)["status"])),
                ["pending", "succeeded"],
            )

    async def test_drain_timeout_pauses_run_for_immediate_handoff(self):
        started = asyncio.Event()

        async def work(ctx):
            if ctx.attempt == 1:
                started.set()
                await asyncio.Event().wait()
            return ctx.input

        workflow = Workflow("drain-timeout", (Task("work", work),))
        with Store(":memory:") as store:
            run_id = store.create(workflow, 42)
            stop = asyncio.Event()
            worker = Worker(store, workflow, poll_interval=0.005, lease_ttl=120)
            serving = asyncio.create_task(
                worker.serve(once=True, stop_event=stop, drain_timeout=0.02)
            )
            await asyncio.wait_for(started.wait(), 1)
            stop.set()
            self.assertEqual(await asyncio.wait_for(serving, 1), [])
            self.assertEqual(store.run(run_id)["status"], "paused")
            self.assertIsNone(store.run(run_id)["owner"])
            self.assertEqual(store.history(run_id)[0]["status"], "interrupted")
            result = await Worker(store, workflow, poll_interval=0.005).serve(once=True)
            self.assertEqual(result[0].status, "succeeded")
            self.assertEqual(result[0].outputs, {"work": 42})

    async def test_invalid_drain_timeout(self):
        workflow = Workflow("settings", (Task("identity", identity),))
        with Store(":memory:") as store:
            worker = Worker(store, workflow)
            for timeout in (-1, math.inf, math.nan, True, "1"):
                with self.assertRaises(ValueError):
                    await worker.serve(once=True, drain_timeout=timeout)


class WorkerProcessTests(unittest.TestCase):
    @unittest.skipIf(sys.platform == "win32", "Windows terminate does not deliver SIGTERM")
    def test_sigterm_drains_current_run_without_claiming_next(self):
        from tests.fixtures.drainable import workflow

        with tempfile.TemporaryDirectory() as directory:
            db = str(Path(directory, "queue.db"))
            marker = str(Path(directory, "started"))
            with Store(db) as store:
                first = store.create(workflow, {"marker": marker, "delay": 0.5})
                second = store.create(
                    workflow, {"marker": str(Path(directory, "next")), "delay": 0}
                )
            child = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "retrace",
                    "--db",
                    db,
                    "worker",
                    "tests.fixtures.drainable:workflow",
                    "--once",
                    "--drain-timeout",
                    "2",
                    "--poll-interval",
                    "0.01",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                deadline = time.monotonic() + 5
                while not Path(marker).exists():
                    if child.poll() is not None or time.monotonic() > deadline:
                        self.fail(f"worker never started: {child.poll()}")
                    time.sleep(0.01)
                os.kill(child.pid, signal.SIGTERM)
                out, err = child.communicate(timeout=5)
                self.assertEqual(child.returncode, 0, err)
                self.assertEqual([item["run_id"] for item in json.loads(out)], [first])
                with Store(db) as store:
                    self.assertEqual(store.run(first)["status"], "succeeded")
                    self.assertEqual(store.run(second)["status"], "pending")
            finally:
                if child.poll() is None:
                    child.kill()
                child.communicate(timeout=5)

    def test_competing_processes_execute_each_queued_run_once(self):
        with tempfile.TemporaryDirectory() as directory:
            db = str(Path(directory, "queue.db"))
            base = [sys.executable, "-m", "retrace", "--db", db, "--lease-ttl", "120"]
            submitted = []
            for number in range(3):
                completed = subprocess.run(
                    base
                    + [
                        "submit",
                        "examples.pipeline:workflow",
                        "--input",
                        json.dumps({"values": [number, number + 1]}),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                submitted.append(json.loads(completed.stdout)["run_id"])
            children = [
                subprocess.Popen(
                    base + ["worker", "examples.pipeline:workflow", "--once"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                for _ in range(2)
            ]
            try:
                for child in children:
                    out, err = child.communicate(timeout=15)
                    self.assertEqual(child.returncode, 0, err)
                    self.assertTrue(isinstance(json.loads(out), list))
                with Store(db) as store:
                    for run_id in submitted:
                        self.assertEqual(store.run(run_id)["status"], "succeeded")
                        self.assertEqual(
                            [attempt["number"] for attempt in store.history(run_id)],
                            [1, 1, 1, 1],
                        )
                        self.assertEqual(
                            len([e for e in store.events(run_id) if e["kind"] == "run.claimed"]),
                            1,
                        )
            finally:
                for child in children:
                    if child.poll() is None:
                        child.kill()
                    child.communicate(timeout=5)

    def test_expired_owner_is_recovered_by_worker_command(self):
        with tempfile.TemporaryDirectory() as directory:
            db = str(Path(directory, "queue.db"))
            # Use the importable example definition, including its fingerprint.
            from examples.pipeline import workflow

            with Store(db) as store:
                run_id = store.create(workflow, {"values": [2, 4]})
                store.claim(run_id, workflow, 0.3)
                store.db.execute("UPDATE runs SET lease_until=0 WHERE id=?", (run_id,))
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "retrace",
                    "--db",
                    db,
                    "worker",
                    "examples.pipeline:workflow",
                    "--once",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(completed.stdout)[0]["run_id"], run_id)
            with Store(db) as store:
                self.assertEqual(store.run(run_id)["epoch"], 2)
                self.assertEqual(store.run(run_id)["status"], "succeeded")
