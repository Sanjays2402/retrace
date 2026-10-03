from __future__ import annotations

import copy
import unittest

from retrace.health import evaluate_health


def cohort():
    return {
        "truncated": False,
        "workflows": [
            {
                "name": "billing",
                "version": "1",
                "fingerprint": "abc",
                "completed_runs": 5,
                "failure_rate": 0.2,
                "p95_completion_seconds": 40,
                "expired_leases": 0,
            }
        ],
    }


class HealthCheckTests(unittest.TestCase):
    def test_inclusive_boundaries_and_observed_values_without_mutation(self):
        metrics = cohort()
        before = copy.deepcopy(metrics)
        checked = evaluate_health(
            metrics, max_failure_rate=0.2, max_p95_seconds=40, max_expired_leases=0
        )
        self.assertEqual(checked["status"], "passed")
        self.assertEqual(checked["reasons"], [])
        self.assertEqual([c["observed"] for c in checked["workflows"][0]["checks"]], [0.2, 40, 0])
        self.assertEqual(metrics, before)

    def test_any_definition_or_threshold_breach_fails(self):
        metrics = cohort()
        second = dict(metrics["workflows"][0], name="other", expired_leases=2)
        metrics["workflows"].append(second)
        checked = evaluate_health(
            metrics, max_failure_rate=0.2, max_p95_seconds=40, max_expired_leases=0
        )
        self.assertEqual(checked["status"], "failed")
        self.assertEqual([w["status"] for w in checked["workflows"]], ["passed", "failed"])
        self.assertEqual(checked["workflows"][1]["checks"][-1]["metric"], "expired_leases")
        self.assertEqual(evaluate_health(cohort(), max_failure_rate=0.1)["status"], "failed")
        self.assertEqual(evaluate_health(cohort(), max_p95_seconds=39)["status"], "failed")

    def test_sample_floor_applies_to_rates_and_latency_not_leases(self):
        metrics = cohort()
        metrics["workflows"][0].update(
            completed_runs=0, failure_rate=None, p95_completion_seconds=None
        )
        checked = evaluate_health(metrics, max_failure_rate=0.1, max_p95_seconds=40)
        self.assertEqual(checked["status"], "insufficient_data")
        self.assertIn("insufficient_completed_runs", checked["reasons"])
        self.assertEqual(evaluate_health(metrics, max_expired_leases=0)["status"], "passed")
        metrics["workflows"][0].update(completed_runs=4, failure_rate=0, p95_completion_seconds=1)
        self.assertEqual(
            evaluate_health(metrics, max_failure_rate=0.1)["status"], "insufficient_data"
        )
        self.assertEqual(
            evaluate_health(metrics, max_failure_rate=0.1, min_completed=4)["status"], "passed"
        )
        metrics["workflows"][0]["expired_leases"] = 1
        checked = evaluate_health(metrics, max_failure_rate=0.1, max_expired_leases=0)
        self.assertEqual(checked["status"], "failed")
        self.assertIn("insufficient_completed_runs", checked["reasons"])

    def test_empty_and_truncated_cohorts_cannot_pass_but_known_breaches_fail(self):
        checked = evaluate_health({"workflows": [], "truncated": False}, max_expired_leases=0)
        self.assertEqual(checked["status"], "insufficient_data")
        self.assertEqual(checked["reasons"], ["empty_cohort"])
        metrics = cohort()
        metrics["truncated"] = True
        checked = evaluate_health(metrics, max_failure_rate=0.2)
        self.assertEqual(checked["status"], "insufficient_data")
        self.assertEqual(checked["reasons"], ["sample_truncated"])
        self.assertEqual(evaluate_health(metrics, max_failure_rate=0.1)["status"], "failed")

    def test_invalid_policy_and_zero_thresholds(self):
        for kwargs in (
            {},
            {"max_failure_rate": 1.1},
            {"max_failure_rate": True},
            {"max_failure_rate": "0.1"},
            {"max_failure_rate": -1},
            {"max_p95_seconds": float("nan")},
            {"max_p95_seconds": float("inf")},
            {"max_expired_leases": -1},
            {"max_expired_leases": 1.5},
            {"max_expired_leases": True},
            {"max_failure_rate": 0, "min_completed": 0},
            {"max_failure_rate": 0, "min_completed": True},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                evaluate_health(cohort(), **kwargs)
        metrics = cohort()
        metrics["workflows"][0].update(failure_rate=0, p95_completion_seconds=0)
        self.assertEqual(
            evaluate_health(metrics, max_failure_rate=0, max_p95_seconds=0)["status"], "passed"
        )
