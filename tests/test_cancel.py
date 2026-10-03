from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from retrace import Engine, LeaseLost, Store, Task, Workflow


async def value(ctx):
    return ctx.input


class CancelTests(unittest.IsolatedAsyncioTestCase):
    async def test_pending_cancel_is_idempotent_and_never_dispatched(self):
        workflow = Workflow("pending", (Task("value", value),))
        with Store(":memory:") as store:
            run_id = store.create(workflow, 42, ready_at=time.time() + 60)
            self.assertTrue(store.cancel(run_id))
            self.assertFalse(store.cancel(run_id))
            self.assertEqual(store.run(run_id)["status"], "cancelled")
            self.assertEqual(store.tasks(run_id)["value"]["status"], "cancelled")
            self.assertIsNone(store.claim_next(workflow, 1))
            self.assertEqual((await Engine(store).resume(workflow, run_id)).status, "cancelled")
            self.assertEqual(
                [event["kind"] for event in store.events(run_id)],
                ["run.created", "run.cancelled"],
            )
            with self.assertRaises(KeyError):
                store.cancel("missing")

    async def test_cancel_fences_owner_and_preserves_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory, "runs.db")
            from tests.fixtures.cancellable import workflow

            with Store(db) as owner, Store(db) as operator:
                run_id = owner.create(workflow, {"directory": directory})
                lease = owner.claim(run_id, workflow, 5)
                owner.start_task(lease, "checkpoint")
                owner.finish_task(lease, "checkpoint", output=42)
                owner.start_task(lease, "slow")
                self.assertTrue(operator.cancel(run_id))
                with self.assertRaises(LeaseLost):
                    owner.finish_task(lease, "slow", output=99)
                self.assertEqual(owner.tasks(run_id)["checkpoint"]["output"], 42)
                self.assertEqual(owner.tasks(run_id)["slow"]["status"], "cancelled")
                self.assertEqual(
                    [attempt["status"] for attempt in owner.history(run_id)],
                    ["succeeded", "interrupted"],
                )
                self.assertEqual(owner.events(run_id)[-1]["kind"], "run.cancelled")
                self.assertIsNone(owner.claim_next(workflow, 5))

    async def test_terminal_runs_cannot_be_cancelled(self):
        workflow = Workflow("terminal", (Task("value", value),))
        with Store(":memory:") as store:
            successful = await Engine(store).run(workflow, 1)
            with self.assertRaisesRegex(ValueError, "completed"):
                store.cancel(successful.run_id)


class CancelProcessTests(unittest.TestCase):
    def test_cancel_running_worker_in_another_process(self):
        with tempfile.TemporaryDirectory() as directory:
            db = str(Path(directory, "runs.db"))
            # This test exercises cancellation, not lease expiry. Give CI workers
            # ample time to start before ownership could expire under load.
            command = [sys.executable, "-m", "retrace", "--db", db, "--lease-ttl", "30"]
            submitted = subprocess.run(
                command
                + [
                    "submit",
                    "tests.fixtures.cancellable:workflow",
                    "--input",
                    json.dumps({"directory": directory}),
                ],
                capture_output=True,
                text=True,
                timeout=20,
            )
            self.assertEqual(submitted.returncode, 0, submitted.stderr)
            run_id = json.loads(submitted.stdout)["run_id"]
            worker = subprocess.Popen(
                command + ["worker", "tests.fixtures.cancellable:workflow", "--once"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                deadline = time.monotonic() + 20
                while not Path(directory, "started").exists():
                    if worker.poll() is not None or time.monotonic() >= deadline:
                        if worker.poll() is None:
                            worker.kill()
                        _out, err = worker.communicate(timeout=5)
                        self.fail(f"worker did not start slow task: {worker.returncode}: {err}")
                    time.sleep(0.02)
                cancelled = subprocess.run(
                    command + ["cancel", run_id], capture_output=True, text=True, timeout=20
                )
                self.assertEqual(cancelled.returncode, 0, cancelled.stderr)
                self.assertTrue(json.loads(cancelled.stdout)["changed"])
                out, err = worker.communicate(timeout=20)
                self.assertEqual(worker.returncode, 0, err)
                self.assertEqual(json.loads(out)[0]["status"], "cancelled")
                self.assertTrue(Path(directory, "cancelled").exists())
                with Store(db) as store:
                    self.assertEqual(store.run(run_id)["status"], "cancelled")
                    self.assertEqual(store.tasks(run_id)["checkpoint"]["output"], 42)
                    self.assertEqual(store.tasks(run_id)["slow"]["status"], "cancelled")
                    self.assertEqual(store.history(run_id)[-1]["status"], "interrupted")
            finally:
                if worker.poll() is None:
                    worker.kill()
                worker.communicate(timeout=5)
