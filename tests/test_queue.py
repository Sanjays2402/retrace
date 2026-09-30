from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from retrace import QueueFull, Store, Task, Worker, Workflow
from retrace.cli import main


async def identity(ctx):
    return ctx.input


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name, "queue.db")
        self.a = Workflow("alpha", (Task("identity", identity),))
        self.b = Workflow("beta", (Task("identity", identity),))

    def tearDown(self):
        self.directory.cleanup()

    def test_round_robin_is_durable_across_store_connections(self):
        with Store(self.path) as first, Store(self.path) as second:
            for number in range(3):
                first.create(self.a, number)
            for number in range(2):
                first.create(self.b, number)
            names = []
            for store in (first, second, first, second, first):
                workflow, lease = store.claim_next_any((self.a, self.b), 120)
                names.append(workflow.name)
                self.assertEqual(store.run(lease.run_id)["status"], "running")
            self.assertEqual(names, ["alpha", "beta", "alpha", "beta", "alpha"])

    def test_active_limit_and_backpressure_are_shared_between_processes(self):
        with Store(self.path) as first, Store(self.path) as second:
            first.configure_queue(self.a, max_active=1, max_queued=2)
            one = first.create(self.a, 1, key="one")
            two = second.create(self.a, 2)
            self.assertEqual(first.create(self.a, 1, key="one"), one)
            with self.assertRaises(QueueFull):
                second.create(self.a, 3)
            lease = first.claim_next(self.a, 120)
            self.assertEqual(lease.run_id, one)
            self.assertIsNone(second.claim_next(self.a, 120))
            three = second.create(self.a, 3)
            self.assertNotEqual(three, two)
            stats = second.queue_stats()[0]
            self.assertEqual((stats["ready"], stats["active"], stats["max_active"]), (2, 1, 1))
            self.assertIsNotNone(stats["oldest_ready_age_seconds"])

    def test_queue_metrics_distinguish_delayed_and_waiting(self):
        with Store(self.path) as store:
            store.create(self.a, 1, ready_at=9_999_999_999)
            stats = store.queue_stats()[0]
            self.assertEqual((stats["ready"], stats["delayed"], stats["active"]), (0, 1, 0))
            self.assertIsNone(stats["oldest_ready_age_seconds"])

    def test_invalid_policy(self):
        with Store(self.path) as store:
            for value in (0, -1, True, 1.5):
                with self.assertRaises(ValueError):
                    store.configure_queue(self.a, max_active=value, max_queued=None)
            with self.assertRaisesRegex(ValueError, "at least one workflow"):
                store.claim_next_any((), 15)
            with self.assertRaisesRegex(ValueError, "unique"):
                store.claim_next_any((self.a, self.a), 15)

    def test_queue_snapshot_reads_pre_migration_database(self):
        with Store(self.path) as store:
            store.create(self.a, 1)
            store.db.execute("DROP TABLE queue_policies")
            store.db.execute("DROP TABLE scheduler_clock")
            store.db.execute("PRAGMA user_version=3")
        with Store(self.path, readonly=True) as reader:
            self.assertEqual(reader.queue_stats()[0]["ready"], 1)

    def test_cli_policy_update_preserves_other_limit(self):
        spec = "examples.pipeline:workflow"
        with redirect_stdout(io.StringIO()):
            self.assertEqual(
                main(
                    [
                        "--db",
                        str(self.path),
                        "queue",
                        "--configure",
                        spec,
                        "--max-active",
                        "2",
                        "--max-queued",
                        "3",
                    ]
                ),
                0,
            )
            self.assertEqual(
                main(["--db", str(self.path), "queue", "--configure", spec, "--max-active", "1"]), 0
            )
        with Store(self.path, readonly=True) as store:
            snapshot = store.queue_stats()[0]
        self.assertEqual((snapshot["max_active"], snapshot["max_queued"]), (1, 3))

    def test_cli_rejects_incomplete_policy_command(self):
        with Store(self.path):
            pass
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(main(["--db", str(self.path), "queue", "--max-active", "1"]), 2)
            self.assertEqual(
                main(
                    ["--db", str(self.path), "queue", "--configure", "examples.pipeline:workflow"]
                ),
                2,
            )


class MultiWorkflowWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_shared_worker_processes_both_definitions(self):
        a = Workflow("alpha", (Task("identity", identity),))
        b = Workflow("beta", (Task("identity", identity),))
        with Store(":memory:") as store:
            ids = {store.create(a, "a"), store.create(b, "b")}
            results = await Worker(store, (a, b), max_runs=2).serve(once=True)
            self.assertEqual({result.run_id for result in results}, ids)
            self.assertTrue(all(result.status == "succeeded" for result in results))

    async def test_invalid_workflow_sets(self):
        a = Workflow("alpha", (Task("identity", identity),))
        with Store(":memory:") as store:
            with self.assertRaisesRegex(ValueError, "at least one Workflow"):
                Worker(store, ())
            with self.assertRaisesRegex(ValueError, "unique"):
                Worker(store, (a, a))


if __name__ == "__main__":
    unittest.main()
