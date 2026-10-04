from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from retrace import Store, Task, Workflow
from retrace.cli import main
from retrace.metrics import prometheus_metrics


async def identity(ctx):
    return ctx.input


class MetricsTests(unittest.TestCase):
    def test_states_capacity_wait_age_and_payload_omission(self):
        with Store(":memory:") as store:
            workflow = Workflow("orders", (Task("step", identity),))
            store.configure_queue(workflow, max_active=2, max_queued=9)
            with patch("retrace.store.time.time", return_value=100):
                for status in ("pending", "paused", "waiting", "running"):
                    run_id = store.create(workflow, {"secret": "PRIVATE_PAYLOAD"})
                    store.db.execute(
                        "UPDATE runs SET status=?,lease_until=150 WHERE id=?", (status, run_id)
                    )
                store.create(workflow, ready_at=500)
                active = store.create(workflow)
                store.db.execute(
                    "UPDATE runs SET status='running',owner='PRIVATE_OWNER',lease_until=500 "
                    "WHERE id=?",
                    (active,),
                )
            with patch("retrace.store.time.time", return_value=200):
                text = prometheus_metrics(store)
            labels = f'workflow="orders",fingerprint="{workflow.fingerprint}"'
            for state in ("ready", "paused", "waiting", "active", "recoverable", "delayed"):
                self.assertIn(f'retrace_queue_runs{{{labels},state="{state}"}} 1\n', text)
            self.assertIn(f'retrace_queue_limit{{{labels},resource="queued"}} 9\n', text)
            self.assertIn(f"retrace_queue_oldest_eligible_seconds{{{labels}}} 100.0\n", text)
            self.assertNotIn("PRIVATE", text)
            self.assertNotIn(active, text)
            self.assertIn("# TYPE retrace_queue_runs gauge\n", text)
            self.assertTrue(text.endswith("\n"))

    def test_escaping_definition_identity_and_absent_limits(self):
        with Store(":memory:") as store:
            name = 'orders"\\\n東京'
            for version in ("1", "2"):
                store.create(
                    Workflow("orders", (Task("step", identity),), version=version), ready_at=1e12
                )
            # Persisted labels must be escaped even if written by another database tool.
            store.db.execute("UPDATE runs SET name=?", (name,))
            text = prometheus_metrics(store)
            self.assertIn('workflow="orders\\"\\\\\\n東京"', text)
            self.assertIn("retrace_queue_definitions 2\n", text)
            samples = [line for line in text.splitlines() if not line.startswith("#")]
            self.assertEqual(len(samples), 13)
            self.assertEqual(len(samples), len(set(samples)))
            self.assertFalse(any(line.startswith("retrace_queue_limit{") for line in samples))
            self.assertFalse(any("oldest_eligible" in line for line in samples))

    def test_empty_database_and_policy_only_queue(self):
        with Store(":memory:") as store:
            self.assertIn("retrace_queue_definitions 0\n", prometheus_metrics(store))
            workflow = Workflow("unused", (Task("step", identity),))
            store.configure_queue(workflow, max_active=1, max_queued=None)
            text = prometheus_metrics(store)
            self.assertIn('resource="active"} 1\n', text)
            self.assertIn('state="ready"} 0\n', text)
            self.assertNotIn('resource="queued"}', text)

    def test_cli_is_readonly_and_missing_database_is_not_created(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "queue.db")
            with redirect_stderr(io.StringIO()):
                self.assertEqual(main(["--db", str(path), "metrics"]), 2)
            self.assertFalse(path.exists())
            with Store(path) as store:
                store.create(Workflow("orders", (Task("step", identity),)))
            before = path.read_bytes()
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["--db", str(path), "metrics"]), 0)
            self.assertIn("retrace_queue_definitions 1\n", output.getvalue())
            self.assertEqual(before, path.read_bytes())
