from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from retrace import Store, Task, Workflow


async def value(ctx):
    return ctx.input


class RunSearchTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name, "runs.db")
        self.store = Store(self.path)
        self.workflow = Workflow("search", (Task("value", value),))

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def test_combined_filters_and_readonly(self):
        pending = self.store.create(self.workflow, {})
        cancelled = self.store.create(self.workflow, {})
        self.store.cancel(cancelled)
        other = self.store.create(Workflow("other", self.workflow.tasks), {})
        self.store.cancel(other)
        version = self.store.create(Workflow("search", self.workflow.tasks, version="2"), {})
        with Store(self.path, readonly=True) as reader:
            self.assertEqual(
                {r["id"] for r in reader.runs(statuses=["pending"], workflow="search")},
                {pending, version},
            )
            self.assertEqual(
                [r["id"] for r in reader.runs(statuses=["cancelled"], workflow="search")],
                [cancelled],
            )
            self.assertEqual(len(reader.runs(statuses=["pending", "cancelled"])), 4)
            self.assertEqual(reader.runs(workflow="missing"), [])
            self.assertEqual(reader.runs(workflow="search' OR 1=1 --"), [])

    def test_cursor_pages_handle_ties_and_new_submissions(self):
        with patch("retrace.store.time.time", return_value=100):
            original = {self.store.create(self.workflow, {}) for _ in range(7)}
        page = self.store.runs(limit=2)
        self.assertEqual([r["id"] for r in page], sorted(original, reverse=True)[:2])
        self.store.create(self.workflow, {})
        seen = [r["id"] for r in page]
        while page:
            page = self.store.runs(limit=2, before=page[-1]["id"])
            seen.extend(r["id"] for r in page)
        self.assertEqual(seen, sorted(original, reverse=True))
        self.assertEqual(len(seen), len(set(seen)))

    def test_cursor_combines_with_filters_and_survives_status_change(self):
        ids = [self.store.create(self.workflow, {}) for _ in range(4)]
        self.store.cancel(ids[1])
        first = self.store.runs(limit=1, statuses=["pending"])[0]
        self.store.cancel(first["id"])
        page = self.store.runs(statuses=["pending"], before=first["id"])
        self.assertEqual([r["id"] for r in page], [ids[2], ids[0]])
        # The cursor need not match the selected workflow or status.
        self.assertEqual(self.store.runs(workflow="missing", before=first["id"]), [])

    def test_invalid_queries_and_existing_limit_bounds(self):
        self.store.create(self.workflow, {})
        for statuses in ([], ["not-a-status"], "failed"):
            with self.assertRaises(ValueError):
                self.store.runs(statuses=statuses)
        for workflow in ("", 42):
            with self.assertRaises(ValueError):
                self.store.runs(workflow=workflow)
        for limit in (True, 2.5, "2"):
            with self.assertRaises(ValueError):
                self.store.runs(limit=limit)
        for cursor in ("missing", "' OR 1=1 --"):
            with self.assertRaises(KeyError):
                self.store.runs(before=cursor)
        self.assertEqual(len(self.store.runs(limit=0)), 1)
        self.assertEqual(len(self.store.runs(limit=10000)), 1)
