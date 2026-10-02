from __future__ import annotations

import asyncio
import hashlib
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from retrace import DefinitionMismatch, Engine, RetryPolicy, Store, Task, Workflow
from retrace.workflow import encode


async def work(ctx):
    return ctx.attempt


class PolicyTests(unittest.TestCase):
    def test_full_jitter_uses_the_capped_exponential_window(self):
        policy = RetryPolicy(initial_delay=2, max_delay=5, jitter=True)
        with patch("retrace.workflow.random.random", return_value=0.5):
            self.assertEqual([policy.delay(i) for i in (1, 2, 3, 10000)], [1, 2, 2.5, 2.5])
        with patch("retrace.workflow.random.random", return_value=0):
            self.assertEqual(policy.delay(1), 0)
        with patch("retrace.workflow.random.random", side_effect=AssertionError("unexpected draw")):
            self.assertEqual(RetryPolicy().delay(1), 0.25)

    def test_validation_and_subclass_classification(self):
        classes = [ValueError]
        policy = RetryPolicy(non_retryable=classes)
        classes.clear()
        self.assertEqual(policy.non_retryable, (ValueError,))
        self.assertFalse(policy.can_retry(UnicodeError("invalid input"), 1))
        self.assertTrue(policy.can_retry(ConnectionError("temporary"), 1))
        self.assertFalse(policy.can_retry(ConnectionError("temporary"), 3))
        for options in (
            {"jitter": 1},
            {"non_retryable": (BaseException,)},
            {"non_retryable": (asyncio.CancelledError,)},
            {"non_retryable": ("ValueError",)},
            {"non_retryable": (ValueError("instance"),)},
        ):
            with self.subTest(options=options), self.assertRaises(ValueError):
                RetryPolicy(**options)

    def test_default_fingerprint_is_compatible_and_options_are_canonical(self):
        legacy = {
            "name": "compatibility",
            "version": "1",
            "tasks": [
                {
                    "name": "step",
                    "needs": [],
                    "function": f"{__name__}:work",
                    "timeout": 60.0,
                    "max_attempts": 3,
                    "initial_delay": 0.25,
                    "max_delay": 30.0,
                }
            ],
        }
        original = Workflow("compatibility", (Task("step", work),))
        self.assertEqual(original.fingerprint, hashlib.sha256(encode(legacy).encode()).hexdigest())
        jittered = Workflow("compatibility", (Task("step", work, retry=RetryPolicy(jitter=True)),))
        self.assertNotEqual(original.fingerprint, jittered.fingerprint)
        policies = [
            RetryPolicy(non_retryable=items)
            for items in ((ValueError, TypeError), (TypeError, ValueError, ValueError))
        ]
        definitions = [
            Workflow("compatibility", (Task("step", work, retry=policy),)) for policy in policies
        ]
        self.assertEqual(definitions[0].fingerprint, definitions[1].fingerprint)
        self.assertNotEqual(original.fingerprint, definitions[0].fingerprint)
        with Store(":memory:") as store:
            run_id = store.create(original)
            with self.assertRaises(DefinitionMismatch):
                store.claim(run_id, jittered, 1)


class PolicyExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_permanent_error_fails_once_and_blocks_only_its_descendants(self):
        async def invalid(ctx):
            raise UnicodeError("invalid input")

        definition = Workflow(
            "permanent",
            (
                Task(
                    "invalid",
                    invalid,
                    retry=RetryPolicy(5, 0, 0, jitter=True, non_retryable=(ValueError,)),
                ),
                Task("dependent", work, needs=("invalid",)),
                Task("independent", work),
            ),
        )
        with Store(":memory:") as store:
            with patch(
                "retrace.workflow.random.random", side_effect=AssertionError("unexpected draw")
            ):
                result = await Engine(store).run(definition)
            self.assertEqual(result.status, "failed")
            self.assertEqual(result.outputs, {"independent": 1})
            self.assertIn("UnicodeError", result.errors["invalid"])
            states = store.tasks(result.run_id)
            self.assertEqual(states["invalid"]["attempts"], 1)
            self.assertEqual(states["dependent"]["status"], "blocked")
            self.assertFalse(
                any(event["kind"] == "task.retrying" for event in store.events(result.run_id))
            )

    async def test_transient_error_still_retries_and_manual_retry_can_reopen_permanent_failure(
        self,
    ):
        errors = [ConnectionError("temporary"), ValueError("permanent")]

        async def flaky(ctx):
            if errors:
                raise errors.pop(0)
            return "fixed"

        definition = Workflow(
            "classified",
            (
                Task(
                    "step",
                    flaky,
                    retry=RetryPolicy(5, 0, 0, jitter=True, non_retryable=(ValueError,)),
                ),
            ),
        )
        with Store(":memory:") as store:
            with patch("retrace.workflow.random.random", return_value=0.5) as draw:
                result = await Engine(store).run(definition)
            self.assertEqual(result.status, "failed")
            self.assertEqual(store.tasks(result.run_id)["step"]["attempts"], 2)
            draw.assert_called_once()
            recovered = await Engine(store).retry(definition, result.run_id)
            self.assertEqual(recovered.outputs, {"step": "fixed"})
            self.assertEqual(store.tasks(result.run_id)["step"]["attempts"], 3)

    async def test_jitter_deadline_is_persisted_once_and_reused_after_reopening(self):
        started = []

        async def transient(ctx):
            started.append(time.time())
            if ctx.attempt == 1:
                raise ConnectionError("busy")
            return ctx.attempt

        definition = Workflow(
            "jittered", (Task("step", transient, retry=RetryPolicy(3, 0.8, 1, jitter=True)),)
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "retry.db")
            with Store(path) as store:
                run_id = store.create(definition)
                lease = store.claim(run_id, definition, 5)
                before = time.time()
                with patch("retrace.workflow.random.random", return_value=0.5) as draw:
                    await Engine(store)._execute(
                        definition.tasks[0], lease, store.tasks(run_id)["step"]
                    )
                after = time.time()
                draw.assert_called_once()
                state = store.tasks(run_id)["step"]
                self.assertEqual(state["status"], "retrying")
                deadline = state["next_at"]
                self.assertGreaterEqual(deadline, before + 0.4)
                self.assertLessEqual(deadline, after + 0.4)
                event = next(e for e in store.events(run_id) if e["kind"] == "task.retrying")
                self.assertEqual(event["payload"]["retry_at"], deadline)
                store.release(lease, "paused")
            with Store(path) as store:
                self.assertEqual(store.tasks(run_id)["step"]["next_at"], deadline)
                with patch(
                    "retrace.workflow.random.random", side_effect=AssertionError("resampled")
                ):
                    result = await Engine(store).resume(definition, run_id)
                self.assertEqual(result.outputs, {"step": 2})
                self.assertGreaterEqual(started[1], deadline)

    async def test_timeout_can_be_classified_as_permanent(self):
        async def slow(ctx):
            await asyncio.sleep(10)

        definition = Workflow(
            "timeout",
            (Task("step", slow, timeout=0.01, retry=RetryPolicy(non_retryable=(TimeoutError,))),),
        )
        with Store(":memory:") as store:
            result = await Engine(store).run(definition)
            self.assertEqual(result.status, "failed")
            self.assertEqual(store.tasks(result.run_id)["step"]["attempts"], 1)
