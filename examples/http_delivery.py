"""Real loopback HTTP delivery; the server deduplicates a deliberately retried request.

Run: python -m examples.http_delivery
The first accepted response is replaced with HTTP 503 to model an ambiguous outcome.
This in-memory teaching server is not a production idempotency store.
"""

import asyncio
import json
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

from retrace import Engine, RetryPolicy, Store, Task, Workflow


async def deliver(ctx):
    def send():
        request = Request(
            ctx.input["url"],
            data=b'{"report":"daily-orders"}',
            headers={"Content-Type": "application/json", "Idempotency-Key": ctx.idempotency_key},
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            return json.load(response)

    return await asyncio.to_thread(send)


workflow = Workflow(
    "http-delivery", (Task("deliver", deliver, retry=RetryPolicy(initial_delay=0.01), timeout=10),)
)


def demonstrate():
    receipts = {}
    request_count = 0
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            nonlocal request_count
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            key = self.headers["Idempotency-Key"]
            with lock:
                request_count += 1
                first = key not in receipts
                receipts.setdefault(key, {"receipt": "accepted-1"})
                body = json.dumps(receipts[key]).encode()
            self.send_response(503 if first else 200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with (
                tempfile.TemporaryDirectory() as directory,
                Store(Path(directory, "run.db")) as store,
            ):
                result = asyncio.run(
                    Engine(store).run(
                        workflow, {"url": f"http://127.0.0.1:{server.server_port}/reports"}
                    )
                )
                report = {
                    "status": result.status,
                    "http_requests": request_count,
                    "accepted_deliveries": len(receipts),
                    "attempts": store.tasks(result.run_id)["deliver"]["attempts"],
                }
                assert report == {
                    "status": "succeeded",
                    "http_requests": 2,
                    "accepted_deliveries": 1,
                    "attempts": 2,
                }, report
                return report
        finally:
            server.shutdown()
            thread.join()


if __name__ == "__main__":
    print(json.dumps(demonstrate(), indent=2))
