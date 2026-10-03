from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from retrace import Engine, RetryPolicy, Store, Task, Workflow
from retrace.report import export_report


async def flaky(ctx):
    if ctx.attempt == 1:
        raise ValueError("secret-error")
    return {"secret-output": ctx.signal}


class ReportTests(unittest.TestCase):
    def test_payload_policy_preserves_checkpoints_attempts_and_signals(self):
        workflow = Workflow(
            "report", (Task("step", flaky, wait_for="go", retry=RetryPolicy(initial_delay=0)),)
        )
        with Store(":memory:") as store:
            run_id = store.create(workflow, {"secret-input": True}, key="secret-key")
            store.signal(run_id, "go", {"secret-signal": True})
            asyncio.run(Engine(store).resume(workflow, run_id))
            before = store.db.total_changes
            report = export_report(store, run_id)
            self.assertEqual(store.db.total_changes, before)
            self.assertEqual(report["report_version"], 1)
            self.assertFalse(report["includes_payloads"])
            self.assertNotIn("secret", json.dumps(report))
            self.assertEqual(report["run"]["status"], "succeeded")
            self.assertEqual([a["status"] for a in report["attempts"]], ["failed", "succeeded"])
            self.assertEqual(report["tasks"]["step"]["attempts"], 2)
            self.assertEqual(report["signals"][0]["name"], "go")
            self.assertFalse(report["journal"]["truncated"])
            full = export_report(store, run_id, include_payloads=True)
            self.assertTrue(full["includes_payloads"])
            self.assertIn("secret-input", full["run"]["input"])
            self.assertIn("secret-output", full["tasks"]["step"]["output"])
            self.assertIn("secret-error", full["attempts"][0]["error"])
            self.assertIn("secret-signal", full["signals"][0]["payload"])
            self.assertTrue(any("secret-error" in json.dumps(e) for e in full["journal"]["events"]))
            for exported in (report, full):
                self.assertNotIn("owner", exported["run"])
                self.assertNotIn("submission_key_hash", exported["run"])

    def test_journal_is_bounded_latest_events_in_ascending_order(self):
        with Store(":memory:") as store:
            run_id = store.create(Workflow("report", (Task("step", flaky),)), None)
            for i in range(1005):
                store.db.execute(
                    "INSERT INTO events(run_id,kind,at,payload) VALUES(?,?,?,?)",
                    (run_id, "custom", i, json.dumps({"secret": i})),
                )
            report = export_report(store, run_id, event_limit=10)
            journal = report["journal"]
            self.assertEqual(journal["total"], 1006)
            self.assertTrue(journal["truncated"])
            self.assertEqual(len(journal["events"]), 10)
            ids = [e["id"] for e in journal["events"]]
            self.assertEqual(ids, list(range(997, 1007)))
            self.assertEqual(len(export_report(store, run_id)["journal"]["events"]), 1000)

    def test_readonly_export_is_coherent_during_concurrent_cancellation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "runs.db")
            with Store(path) as writer:
                run_id = writer.create(Workflow("report", (Task("step", flaky),)), {})
                with Store(path, readonly=True) as reader:
                    original = reader.tasks

                    def cancel_then_read(run_id):
                        writer.cancel(run_id)
                        return original(run_id)

                    with patch.object(reader, "tasks", side_effect=cancel_then_read):
                        report = export_report(reader, run_id)
                    self.assertEqual(report["run"]["status"], "pending")
                    self.assertEqual(report["tasks"]["step"]["status"], "pending")
                    self.assertEqual(report["journal"]["total"], 1)
                    self.assertEqual(writer.run(run_id)["status"], "cancelled")
                    self.assertFalse(reader.db.in_transaction)

    def test_unknown_run_and_invalid_options(self):
        with Store(":memory:") as store:
            for limit in (0, 1001, True, 1.5):
                with self.assertRaises(ValueError):
                    export_report(store, "missing", event_limit=limit)
            with self.assertRaises(ValueError):
                export_report(store, "missing", include_payloads=1)
            with self.assertRaises(KeyError):
                export_report(store, "missing")
            self.assertFalse(store.db.in_transaction)
