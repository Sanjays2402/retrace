import asyncio
import json
import unittest

from retrace import Engine, RetryPolicy, Store, Task, Workflow
from retrace.trace import export_trace


async def flaky(ctx):
    if ctx.attempt == 1:
        raise ValueError("private exception")
    return "private output"


class TraceTests(unittest.TestCase):
    def test_retry_history_units_lanes_and_data_minimization(self):
        with Store(":memory:") as store:
            result = asyncio.run(
                Engine(store).run(
                    Workflow("trace", (Task("step", flaky, retry=RetryPolicy(initial_delay=0)),)),
                    "private input",
                )
            )
            before = store.db.total_changes
            trace = export_trace(store, result.run_id)
            self.assertEqual(store.db.total_changes, before)
            spans = [e for e in trace["traceEvents"] if e["ph"] == "X"]
            self.assertEqual([e["args"]["status"] for e in spans], ["failed", "succeeded"])
            history = store.history(result.run_id)
            for span, attempt in zip(spans, history, strict=True):
                self.assertEqual(
                    span["dur"], round((attempt["finished_at"] - attempt["started_at"]) * 1_000_000)
                )
                self.assertEqual(span["tid"], 1)
            self.assertNotIn("private", json.dumps(trace))

    def test_pending_and_unfinished_attempts(self):
        workflow = Workflow("trace", (Task("step", flaky),))
        with Store(":memory:") as store:
            run_id = store.create(workflow, None)
            self.assertFalse(
                any(e["ph"] == "X" for e in export_trace(store, run_id)["traceEvents"])
            )
            lease = store.claim(run_id, workflow, 15)
            store.start_task(lease, "step")
            event = export_trace(store, run_id)["traceEvents"][-1]
            self.assertEqual(event["ph"], "I")
            self.assertNotIn("dur", event)
            with self.assertRaises(KeyError):
                export_trace(store, "missing")
