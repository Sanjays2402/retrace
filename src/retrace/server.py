"""Read-only, loopback-only inspection server. Never imports workflow code."""

from __future__ import annotations

import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from retrace.store import Store
from retrace.trace import export_trace

_ASSETS = {
    "/": ("index.html", "text/html"),
    "/app.js": ("app.js", "text/javascript"),
    "/style.css": ("style.css", "text/css"),
    "/mark.svg": ("mark.svg", "image/svg+xml"),
}


def make_server(db_path: str | Path, port: int = 7760) -> ThreadingHTTPServer:
    path = Path(db_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"database not found: {path}; run a workflow first")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_: object) -> None:
            pass

        def send_body(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", f"{content_type}; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; "
                "style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'",
            )
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, status: int, data: object) -> None:
            self.send_body(status, json.dumps(data, allow_nan=False).encode(), "application/json")

        def do_GET(self) -> None:
            port = self.server.server_address[1]
            allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
            # Prevent a hostile website from using DNS rebinding to read local run data.
            if self.headers.get("Host", "") not in allowed:
                self.send_json(403, {"error": "invalid host"})
                return
            origin = self.headers.get("Origin")
            if origin is not None and origin not in {f"http://{host}" for host in allowed}:
                self.send_json(403, {"error": "cross-origin requests are disabled"})
                return
            url = urlsplit(self.path)
            if url.path in _ASSETS:
                asset, content_type = _ASSETS[url.path]
                self.send_body(
                    200, files("retrace").joinpath("static", asset).read_bytes(), content_type
                )
                return
            parts = url.path.strip("/").split("/")
            try:
                with Store(path, readonly=True) as store:
                    if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "trace":
                        self.send_json(200, export_trace(store, unquote(parts[2])))
                        return
                    # A read transaction gives the UI a coherent multi-table snapshot.
                    store.db.execute("BEGIN")
                    if parts == ["api", "runs"]:
                        self.send_json(200, {"runs": store.runs()})
                    elif len(parts) in (3, 4) and parts[:2] == ["api", "runs"]:
                        run_id = unquote(parts[2])
                        run = store.run(run_id)
                        if len(parts) == 3:
                            run.pop("owner", None)
                            run.pop("submission_key_hash", None)
                            self.send_json(
                                200,
                                {
                                    "run": run,
                                    "tasks": store.tasks(run_id),
                                    "attempts": store.history(run_id),
                                },
                            )
                        elif parts[3] == "events":
                            after = int(parse_qs(url.query).get("after", ["0"])[0])
                            events = store.events(run_id, after=after)
                            self.send_json(
                                200,
                                {"events": events, "cursor": events[-1]["id"] if events else after},
                            )
                        else:
                            self.send_json(404, {"error": "route not found"})
                    else:
                        self.send_json(404, {"error": "route not found"})
            except KeyError:
                self.send_json(404, {"error": "run not found"})
            except ValueError:
                self.send_json(400, {"error": "invalid request"})
            except sqlite3.Error:
                self.send_json(503, {"error": "database unavailable; retry shortly"})

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def serve(db_path: str, port: int = 7760) -> None:
    with make_server(db_path, port) as server:
        print(
            f"Retrace inspector · http://127.0.0.1:{server.server_address[1]} · read-only",
            flush=True,
        )
        server.serve_forever()
