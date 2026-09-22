# Real integrations

Clone the repository and install it with `python -m pip install -e .` so the example
modules are available. Run the following commands from the repository root.

## CSV orders

```sh
retrace run examples.csv_pipeline:workflow --input '{"path":"examples/data/orders.csv"}'
```

On Windows PowerShell, put the JSON in a variable or use Python's API to avoid shell
quoting differences. Expected summary: 3 orders, 5000 cents. Amounts use integers,
not floating point. Duplicate IDs and negative amounts fail validation. File I/O runs
in a thread so it does not block the scheduler's heartbeat.

The extraction checkpoint stores the rows, so recovery uses the original snapshot even
if the source file changes. This small example caps inputs at 1000 rows and the engine
also enforces its 1 MiB JSON limit. For large data, checkpoint immutable artifact paths
and checksums, and batch the work. Never overwrite an artifact referenced by a checkpoint.

## HTTP delivery with an ambiguous response

```sh
python -m examples.http_delivery
```

Expected output: `status=succeeded`, `http_requests=2`, `accepted_deliveries=1`,
`attempts=2`. A real loopback HTTP server accepts the first request but returns 503.
Retrace retries with the same `ctx.idempotency_key`; the receiver returns the existing
receipt instead of accepting a second delivery. No external account or service is used.

The receiver's in-memory map is only a teaching aid. A production receiver must atomically
persist the key, request identity, effect, and receipt, reject key reuse with a different
payload, and retain keys for the entire retry/recovery window. Retrace cannot provide
exactly-once external effects by itself. Threaded blocking HTTP calls may continue after
async cancellation; downstream idempotency remains necessary.
