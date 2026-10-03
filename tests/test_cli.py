from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from retrace import Store
from retrace.cli import load_workflow, main


class CLITests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db = str(Path(self.directory.name, "runs.db"))

    def tearDown(self):
        self.directory.cleanup()

    def invoke(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--db", self.db, *args])
        return code, out.getvalue(), err.getvalue()

    def test_backup_uses_readonly_source_and_refuses_to_overwrite(self):
        destination = str(Path(self.directory.name, "backup.db"))
        self.assertEqual(self.invoke("backup", destination)[0], 2)
        self.assertFalse(Path(self.db).exists())
        self.assertFalse(Path(destination).exists())
        self.assertEqual(self.invoke("submit", "examples.pipeline:workflow")[0], 0)
        code, out, err = self.invoke("backup", destination, "--timeout", "10")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["runs"], 1)
        self.assertEqual(json.loads(out)["size_bytes"], Path(destination).stat().st_size)
        before = Path(destination).read_bytes()
        self.assertEqual(self.invoke("backup", destination)[0], 2)
        self.assertEqual(Path(destination).read_bytes(), before)
        self.assertEqual(self.invoke("backup", destination + ".new", "--timeout", "nan")[0], 2)
        with Store(destination, readonly=True) as restored:
            self.assertEqual(len(restored.runs()), 1)

    def test_prune_defaults_to_preview_and_requires_explicit_apply(self):
        self.assertEqual(self.invoke("prune", "--older-than", "30")[0], 2)
        self.assertFalse(Path(self.db).exists())
        self.assertEqual(self.invoke("submit", "examples.pipeline:workflow")[0], 0)
        with Store(self.db) as store:
            run_id = store.runs()[0]["id"]
            store.cancel(run_id)
            store.db.execute("UPDATE runs SET updated_at=0 WHERE id=?", (run_id,))
        code, out, err = self.invoke("prune", "--older-than", "30")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["run_ids"], [run_id])
        self.assertFalse(json.loads(out)["applied"])
        self.assertEqual(len(json.loads(self.invoke("runs")[1])), 1)
        code, out, err = self.invoke("prune", "--older-than", "30", "--apply")
        self.assertEqual(code, 0, err)
        self.assertTrue(json.loads(out)["applied"])
        self.assertEqual(json.loads(out)["run_ids"], [run_id])
        self.assertEqual(json.loads(self.invoke("runs")[1]), [])
        for days in ("0", "-1", "nan", "inf"):
            self.assertEqual(self.invoke("prune", "--older-than", days)[0], 2)
        self.assertEqual(self.invoke("prune", "--older-than", "30", "--limit", "501")[0], 2)

    def test_run_inspect_resume_list_and_events(self):
        code, out, err = self.invoke(
            "run", "examples.pipeline:workflow", "--input", '{"values":[2,4]}'
        )
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertEqual(result["outputs"]["summarize"]["mean"], 3)
        run_id = result["run_id"]
        self.assertEqual(json.loads(self.invoke("runs")[1])[0]["id"], run_id)
        self.assertEqual(
            json.loads(self.invoke("inspect", run_id)[1])["tasks"]["total"]["output"], 6
        )
        self.assertEqual(self.invoke("resume", "examples.pipeline:workflow", run_id)[0], 0)
        trace = json.loads(self.invoke("trace", run_id)[1])
        self.assertEqual(len([e for e in trace["traceEvents"] if e["ph"] == "X"]), 4)
        events = [json.loads(line) for line in self.invoke("events", run_id)[1].splitlines()]
        self.assertEqual(events[-1]["kind"], "run.succeeded")
        self.assertEqual(self.invoke("events", run_id, "--after", str(events[-1]["id"]))[1], "")

    def test_runs_limit(self):
        for values in ([1], [2], [3]):
            self.assertEqual(
                self.invoke(
                    "run", "examples.pipeline:workflow", "--input", json.dumps({"values": values})
                )[0],
                0,
            )
        limited = json.loads(self.invoke("runs", "--limit", "2")[1])
        self.assertEqual(len(limited), 2)
        full = json.loads(self.invoke("runs")[1])
        self.assertEqual(len(full), 3)
        self.assertEqual(limited, full[:2])

    def test_runs_filters_and_cursor(self):
        for _ in range(3):
            self.assertEqual(self.invoke("submit", "examples.pipeline:workflow")[0], 0)
        all_runs = json.loads(self.invoke("runs")[1])
        self.invoke("cancel", all_runs[1]["id"])
        with Store(self.db, readonly=True) as store:
            name = store.run(all_runs[0]["id"])["name"]
        code, out, err = self.invoke("runs", "--status", "pending", "--workflow", name)
        self.assertEqual(code, 0, err)
        page = json.loads(out)
        self.assertEqual([r["id"] for r in page], [all_runs[0]["id"], all_runs[2]["id"]])
        page = json.loads(
            self.invoke(
                "runs",
                "--before",
                all_runs[0]["id"],
                "--status",
                "pending",
                "--status",
                "cancelled",
            )[1]
        )
        self.assertEqual([r["id"] for r in page], [r["id"] for r in all_runs[1:]])
        for args in (("--status", "typo"), ("--before", "missing"), ("--workflow", "")):
            self.assertEqual(self.invoke("runs", *args)[0], 2)

    def test_submit_and_worker_once(self):
        code, out, err = self.invoke(
            "submit", "examples.pipeline:workflow", "--input", '{"values":[2,4]}'
        )
        self.assertEqual(code, 0, err)
        queued = json.loads(out)
        self.assertEqual(queued["status"], "pending")
        with Store(self.db) as store:
            self.assertEqual(store.run(queued["run_id"])["status"], "pending")
        code, out, err = self.invoke(
            "worker", "examples.pipeline:workflow", "--once", "--max-runs", "2"
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)[0]["run_id"], queued["run_id"])
        self.assertEqual(json.loads(out)[0]["outputs"]["summarize"]["mean"], 3)
        self.assertEqual(
            json.loads(self.invoke("worker", "examples.pipeline:workflow", "--once")[1]), []
        )
        self.assertEqual(
            self.invoke("worker", "examples.pipeline:workflow", "--max-runs", "0")[0], 2
        )

    def test_idempotent_delayed_submit(self):
        args = (
            "submit",
            "examples.pipeline:workflow",
            "--input",
            '{"values":[2,4]}',
            "--key",
            "daily-42",
            "--delay",
            "60",
        )
        code, out, err = self.invoke(*args)
        self.assertEqual(code, 0, err)
        first = json.loads(out)
        self.assertEqual(first["status"], "pending")
        self.assertEqual(self.invoke("worker", "examples.pipeline:workflow", "--once")[1], "[]\n")
        self.assertEqual(json.loads(self.invoke(*args)[1]), first)
        self.assertEqual(
            self.invoke("submit", "examples.pipeline:workflow", "--key", "daily-42")[0], 2
        )
        for invalid in ("-1", "nan", "inf"):
            self.assertEqual(
                self.invoke("submit", "examples.pipeline:workflow", "--delay", invalid)[0], 2
            )
        with Store(self.db) as store:
            store.db.execute("UPDATE runs SET ready_at=0 WHERE id=?", (first["run_id"],))
        code, out, err = self.invoke("worker", "examples.pipeline:workflow", "--once")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)[0]["run_id"], first["run_id"])

    def test_cancel_pending_run(self):
        run_id = json.loads(
            self.invoke("submit", "examples.pipeline:workflow", "--input", '{"values":[2,4]}')[1]
        )["run_id"]
        code, out, err = self.invoke("cancel", run_id)
        self.assertEqual(code, 0, err)
        self.assertEqual(
            json.loads(out), {"run_id": run_id, "status": "cancelled", "changed": True}
        )
        self.assertFalse(json.loads(self.invoke("cancel", run_id)[1])["changed"])
        self.assertEqual(
            json.loads(self.invoke("worker", "examples.pipeline:workflow", "--once")[1]), []
        )
        code, out, _ = self.invoke("resume", "examples.pipeline:workflow", run_id)
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["status"], "cancelled")
        self.assertEqual(self.invoke("cancel", "missing")[0], 2)

    def test_invalid_input_definition_and_unknown_run(self):
        for args in (
            ("run", "bad"),
            ("run", "examples.pipeline:workflow", "--input", "{"),
            ("inspect", "missing"),
            ("events", "missing"),
            ("trace", "missing"),
            ("run", "missing_module:workflow"),
        ):
            code, _, err = self.invoke(*args)
            self.assertEqual(code, 2)
            self.assertIn("retrace:", err)
        with self.assertRaises(TypeError):
            load_workflow("retrace:Engine")

    def test_task_failure_sets_exit_code(self):
        code, out, _ = self.invoke("run", "examples.pipeline:workflow", "--input", "{}")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["status"], "failed")

    def test_demo_runs_with_recorded_retry(self):
        code, out, err = self.invoke("demo")
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertEqual(result["outputs"]["report"]["vectors"], 512)
        with Store(self.db) as store:
            self.assertEqual(store.tasks(result["run_id"])["embed"]["attempts"], 2)

    def test_keyboard_interrupt_and_serve(self):
        with patch("retrace.server.serve", side_effect=KeyboardInterrupt):
            code, _, err = self.invoke("serve")
            self.assertEqual(code, 130)
            self.assertIn("Interrupted", err)
        with patch("retrace.server.serve") as serve:
            self.assertEqual(self.invoke("serve", "--port", "7761")[0], 0)
            serve.assert_called_once_with(self.db, 7761)

    def test_retry_preview_and_execution_preserve_completed_task(self):
        ready_file = Path(self.directory.name, "service.ready")
        code, out, _ = self.invoke(
            "run",
            "examples.recoverable:workflow",
            "--input",
            json.dumps({"ready_file": str(ready_file)}),
        )
        self.assertEqual(code, 1)
        run_id = json.loads(out)["run_id"]
        code, out, _ = self.invoke(
            "retry", "examples.recoverable:workflow", run_id, "--task", "publish", "--dry-run"
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["preserved"], ["extract"])
        with Store(self.db) as store:
            self.assertEqual(store.run(run_id)["status"], "failed")
        ready_file.touch()
        code, out, err = self.invoke(
            "retry", "examples.recoverable:workflow", run_id, "--task", "publish"
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["outputs"]["publish"]["published"], 128)
        with Store(self.db) as store:
            self.assertEqual(store.tasks(run_id)["extract"]["attempts"], 1)
            self.assertEqual(store.tasks(run_id)["publish"]["attempts"], 3)
        self.assertEqual(self.invoke("retry", "examples.recoverable:workflow", run_id)[0], 2)

    def test_read_commands_and_dry_run_do_not_create_database(self):
        for args in (
            ("runs",),
            ("inspect", "missing"),
            ("events", "missing"),
            ("trace", "missing"),
            ("retry", "examples.recoverable:workflow", "missing", "--dry-run"),
        ):
            self.assertEqual(self.invoke(*args)[0], 2)
            self.assertFalse(Path(self.db).exists())
