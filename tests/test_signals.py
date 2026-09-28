from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from retrace import Engine, Store, Task, Worker, Workflow
from retrace.cli import main


async def receive(ctx):
    return ctx.signal


class SignalTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "signals.db"
        self.store = Store(self.path)
        self.workflow = Workflow("approval", (Task("approve", receive, wait_for="approval"),))

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    async def test_wait_releases_lease_and_wakes_local_worker(self):
        run_id = self.store.create(self.workflow)
        first = await Engine(self.store).resume(self.workflow, run_id)
        self.assertEqual(first.status, "waiting")
        self.assertIsNone(self.store.run(run_id)["owner"])
        self.assertEqual(self.store.tasks(run_id)["approve"]["attempts"], 0)
        with Store(self.path) as sender:
            self.assertTrue(sender.signal(run_id, "approval", {"approved": True}))
        self.assertEqual(self.store.run(run_id)["status"], "pending")
        with Store(self.path) as worker_store:
            results = await Worker(worker_store, self.workflow).serve(once=True)
        self.assertEqual(results[0].outputs["approve"], {"approved": True})
        self.assertEqual(self.store.run(run_id)["status"], "succeeded")

    async def test_signal_before_wait_and_null_payload(self):
        run_id = self.store.create(self.workflow)
        self.assertTrue(self.store.signal(run_id, "approval"))
        result = await Engine(self.store).resume(self.workflow, run_id)
        self.assertEqual(result.status, "succeeded")
        self.assertIn("approve", result.outputs)
        self.assertIsNone(result.outputs["approve"])
        self.assertNotIn("task.waiting", [e["kind"] for e in self.store.events(run_id)])

    async def test_duplicate_conflict_and_cancelled_run(self):
        run_id = self.store.create(self.workflow)
        self.assertTrue(self.store.signal(run_id, "approval", 1))
        self.assertFalse(self.store.signal(run_id, "approval", 1))
        with self.assertRaises(ValueError):
            self.store.signal(run_id, "approval", 2)
        with self.assertRaises(ValueError):
            self.store.signal(run_id, "unknown", 1)
        other = self.store.create(self.workflow)
        self.assertEqual((await Engine(self.store).resume(self.workflow, other)).status, "waiting")
        self.store.cancel(other)
        self.assertEqual(self.store.tasks(other)["approve"]["status"], "cancelled")
        with self.assertRaises(ValueError):
            self.store.signal(other, "approval", 1)

    async def test_dependency_and_signal_survive_restart(self):
        calls = []

        async def prepare(ctx):
            calls.append("prepare")
            return 7

        async def finish(ctx):
            calls.append("finish")
            return ctx.dependencies["prepare"] + ctx.signal["increment"]

        workflow = Workflow(
            "staged",
            (Task("prepare", prepare), Task("finish", finish, needs=("prepare",), wait_for="go")),
        )
        run_id = self.store.create(workflow)
        self.assertEqual((await Engine(self.store).resume(workflow, run_id)).status, "waiting")
        self.assertEqual(calls, ["prepare"])
        with Store(self.path) as reopened:
            reopened.signal(run_id, "go", {"increment": 3})
            result = await Engine(reopened).resume(workflow, run_id)
        self.assertEqual(result.outputs["finish"], 10)
        self.assertEqual(calls, ["prepare", "finish"])

    async def test_signal_release_race_keeps_run_eligible(self):
        run_id = self.store.create(self.workflow)
        lease = self.store.claim(run_id, self.workflow, 5)
        self.assertTrue(self.store.mark_waiting(lease, "approve", "approval"))
        with Store(self.path) as sender:
            sender.signal(run_id, "approval", True)
        self.store.release(lease, "waiting")
        self.assertEqual(self.store.run(run_id)["status"], "pending")
        result = await Engine(self.store).resume(self.workflow, run_id)
        self.assertEqual(result.status, "succeeded")

    async def test_retry_keeps_original_signal(self):
        async def flaky(ctx):
            if ctx.attempt == 1:
                raise RuntimeError("transient")
            return ctx.signal

        workflow = Workflow("retry_signal", (Task("step", flaky, wait_for="go"),))
        run_id = self.store.create(workflow)
        self.store.signal(run_id, "go", "payload")
        result = await Engine(self.store).resume(workflow, run_id)
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.outputs["step"], "payload")
        self.assertEqual(self.store.tasks(run_id)["step"]["attempts"], 2)

    async def test_cli_delivery_and_inspection(self):
        run_id = self.store.create(self.workflow)
        await Engine(self.store).resume(self.workflow, run_id)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(
                main(
                    [
                        "--db",
                        str(self.path),
                        "signal",
                        run_id,
                        "approval",
                        "--payload",
                        '{"ok":true}',
                    ]
                ),
                0,
            )
        self.assertIn('"delivered": true', output.getvalue())
        self.assertEqual(self.store.signals(run_id)[0]["payload"], {"ok": True})

    async def test_version_two_database_migrates_on_write(self):
        run_id = self.store.create(self.workflow)
        self.store.db.execute("DROP TABLE signals")
        self.store.db.execute("PRAGMA user_version=2")
        with Store(self.path, readonly=True) as reader:
            self.assertEqual(reader.signals(run_id), [])
        with Store(self.path) as upgraded:
            self.assertEqual(upgraded.schema_version, 3)
            self.assertTrue(upgraded.signal(run_id, "approval", "ready"))
            self.assertEqual(upgraded.run(run_id)["status"], "pending")


if __name__ == "__main__":
    unittest.main()
