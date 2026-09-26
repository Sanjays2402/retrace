from __future__ import annotations

import asyncio
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from retrace import Engine, Store, Task, Workflow
from retrace.server import make_server


async def value(ctx):
    return {"value": "<script>alert(1)</script>"}


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name, "runs.db")
        with Store(self.path) as store:
            result = asyncio.run(Engine(store).run(Workflow("test", (Task("step", value),))))
            self.run_id = result.run_id
        self.server = make_server(self.path, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.directory.cleanup()

    def request(self, path, method="GET", headers=None):
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_address[1], timeout=5
        )
        connection.request(method, path, headers=headers or {})
        response = connection.getresponse()
        status, headers, body = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return status, headers, body

    def test_assets_and_security_headers(self):
        for path in ("/", "/style.css", "/app.js", "/mark.svg"):
            status, headers, body = self.request(path)
            self.assertEqual(status, 200)
            self.assertTrue(body)
            self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
            self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])

    def test_snapshot_and_event_cursor(self):
        status, _, body = self.request("/api/runs")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["runs"][0]["id"], self.run_id)
        status, _, body = self.request("/api/runs/" + self.run_id)
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertNotIn("owner", data["run"])
        self.assertNotIn("submission_key_hash", data["run"])
        self.assertEqual(data["tasks"]["step"]["output"]["value"], "<script>alert(1)</script>")
        status, _, body = self.request(f"/api/runs/{self.run_id}/events")
        page = json.loads(body)
        self.assertEqual(len(page["events"]), 5)
        _, _, body = self.request(f"/api/runs/{self.run_id}/events?after={page['cursor']}")
        self.assertEqual(json.loads(body)["events"], [])

    def test_rejects_cross_origin_and_dns_rebinding(self):
        for headers in ({"Host": "evil.example"}, {"Origin": "https://evil.example"}):
            status, _, _ = self.request("/api/runs", headers=headers)
            self.assertEqual(status, 403)

    def test_no_mutations_traversal_or_unknown_routes(self):
        for path, expected in (
            ("/api/runs/missing", 404),
            ("/../../LICENSE", 404),
            ("/api/runs/" + self.run_id + "/nope", 404),
            (f"/api/runs/{self.run_id}/events?after=nope", 400),
        ):
            self.assertEqual(self.request(path)[0], expected)
        self.assertEqual(self.request("/api/runs", method="POST")[0], 501)

    def test_missing_database_does_not_create_one(self):
        path = Path(self.directory.name, "missing.db")
        with self.assertRaises(FileNotFoundError):
            make_server(path)
        self.assertFalse(path.exists())

    def test_trace_export_preserves_attempts_without_private_output(self):
        status, headers, body = self.request(f"/api/runs/{self.run_id}/trace")
        self.assertEqual(status, 200)
        self.assertIn("application/json", headers["Content-Type"])
        trace = json.loads(body)
        self.assertEqual(trace["retrace"]["run_id"], self.run_id)
        self.assertEqual(len([e for e in trace["traceEvents"] if e["ph"] == "X"]), 1)
        self.assertNotIn("<script>", body.decode())
        self.assertEqual(self.request("/api/runs/missing/trace")[0], 404)
        self.assertEqual(
            self.request(
                f"/api/runs/{self.run_id}/trace", headers={"Origin": "https://evil.example"}
            )[0],
            403,
        )
