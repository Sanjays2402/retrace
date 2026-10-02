import asyncio
import tempfile
import unittest
from pathlib import Path

from examples.classified_retry import workflow as classified_retry
from examples.csv_pipeline import workflow
from examples.http_delivery import demonstrate
from retrace import Engine, Store


class ExampleTests(unittest.TestCase):
    def test_csv_exact_totals_and_resume_reuses_input(self):
        with tempfile.TemporaryDirectory() as directory, Store(":memory:") as store:
            path = Path(directory, "orders.csv")
            path.write_text("order_id,amount_cents\na,1250\nb,2999\n", encoding="utf-8")
            engine = Engine(store)
            result = asyncio.run(engine.run(workflow, {"path": str(path)}))
            self.assertEqual(result.outputs["summarize"], {"orders": 2, "total_cents": 4249})
            path.unlink()
            resumed = asyncio.run(engine.resume(workflow, result.run_id))
            self.assertEqual(result.outputs, resumed.outputs)

    def test_csv_duplicate_ids_fail_validation(self):
        with tempfile.TemporaryDirectory() as directory, Store(":memory:") as store:
            path = Path(directory, "orders.csv")
            path.write_text("order_id,amount_cents\na,1\na,2\n", encoding="utf-8")
            result = asyncio.run(Engine(store).run(workflow, {"path": str(path)}))
            self.assertEqual(result.status, "failed")
            self.assertEqual(store.tasks(result.run_id)["summarize"]["status"], "blocked")

    def test_http_ambiguous_success_is_deduplicated(self):
        self.assertEqual(demonstrate()["accepted_deliveries"], 1)

    def test_classified_retry_example_distinguishes_transient_and_permanent_errors(self):
        with Store(":memory:") as store:
            engine = Engine(store)
            valid = asyncio.run(engine.run(classified_retry, {"count": 128}))
            self.assertEqual(valid.outputs, {"fetch": {"records": 128}})
            self.assertEqual(store.tasks(valid.run_id)["fetch"]["attempts"], 2)
            for input in ({"count": -1}, {"count": "128"}, {}):
                with self.subTest(input=input):
                    invalid = asyncio.run(engine.run(classified_retry, input))
                    self.assertEqual(invalid.status, "failed")
                    self.assertEqual(store.tasks(invalid.run_id)["fetch"]["attempts"], 1)
