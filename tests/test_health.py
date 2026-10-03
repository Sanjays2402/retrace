from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from retrace import Store, Task, Workflow
from retrace.health import workflow_health


async def value(ctx):
    return ctx.input


class HealthTests(unittest.TestCase):
    def test_failure_rates_latency_recovery_and_expired_leases(self):
        with Store(":memory:") as store:
            workflow = Workflow("billing", (Task("step", value),))
            statuses = ["succeeded", "succeeded", "failed", "cancelled", "waiting", "running"]
            ids = []
            with patch("retrace.store.time.time", return_value=100):
                for status, duration in zip(statuses, [10, 20, 100, 1000, 0, 0], strict=True):
                    run_id = store.create(workflow, {})
                    ids.append(run_id)
                    store.db.execute(
                        "UPDATE runs SET status=?,updated_at=?,lease_until=200 WHERE id=?",
                        (status, 100 + duration, run_id),
                    )
            for run_id, status in ((ids[0], "failed"), (ids[0], "interrupted"), (ids[2], "failed")):
                number = (
                    store.db.execute(
                        "SELECT COUNT(*) FROM attempts WHERE run_id=?", (run_id,)
                    ).fetchone()[0]
                    + 1
                )
                store.db.execute(
                    "INSERT INTO attempts(run_id,task_name,number,epoch,status,"
                    "started_at,finished_at) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (run_id, "step", number, 1, status, 100, 101),
                )
            with patch("retrace.health.time.time", return_value=500):
                health = workflow_health(store, since=100)
            item = health["workflows"][0]
            self.assertEqual(item["completed_runs"], 3)
            self.assertAlmostEqual(item["failure_rate"], 1 / 3)
            self.assertEqual(item["p50_completion_seconds"], 20)
            self.assertEqual(item["p95_completion_seconds"], 100)
            self.assertEqual(item["failed_attempts"], 2)
            self.assertEqual(item["interrupted_attempts"], 1)
            self.assertEqual(item["recovered_runs"], 1)
            self.assertEqual(item["expired_leases"], 1)
            self.assertFalse(health["truncated"])

    def test_cohort_sampling_and_definition_groups_are_explicit(self):
        with Store(":memory:") as store:
            with patch("retrace.store.time.time", return_value=100):
                old = store.create(Workflow("billing", (Task("step", value),)), {})
            with patch("retrace.store.time.time", return_value=200):
                store.create(Workflow("billing", (Task("step", value),), version="2"), {})
                store.create(
                    Workflow("billing", (Task("step", value, timeout=1),), version="2"), {}
                )
            health = workflow_health(store, since=101)
            self.assertEqual(health["matched_runs"], 2)
            self.assertEqual(len(health["workflows"]), 2)
            for item in health["workflows"]:
                self.assertIsNone(item["failure_rate"])
                self.assertIsNone(item["p95_completion_seconds"])
                self.assertIsNone(item["p50_completion_seconds"])
            sampled = workflow_health(store, limit=1)
            self.assertTrue(sampled["truncated"])
            self.assertEqual(sampled["matched_runs"], 3)
            self.assertEqual(sampled["sampled_runs"], 1)
            self.assertEqual(sampled["workflows"][0]["version"], "2")
            self.assertEqual(store.run(old)["status"], "pending")

    def test_readonly_empty_and_input_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "runs.db")
            with Store(path):
                pass
            with Store(path, readonly=True) as store:
                before = store.db.total_changes
                health = workflow_health(store)
                self.assertEqual(health["workflows"], [])
                self.assertEqual(health["matched_runs"], 0)
                self.assertEqual(store.db.total_changes, before)
                for since in (-1, float("nan"), float("inf"), True, "1"):
                    with self.assertRaises(ValueError):
                        workflow_health(store, since=since)
                for limit in (0, 10001, True, 2.5):
                    with self.assertRaises(ValueError):
                        workflow_health(store, limit=limit)
