# Contributing to Retrace

Contributions are welcome, especially minimal reproductions, failure-injection tests,
clearer execution guarantees, and improvements to the local inspector.

## Set up

Use Python 3.11 or newer, fork/clone the repository, create a virtual environment, and run:

```bash
python -m pip install -e '.[dev]'
ruff check .
ruff format --check .
python -m coverage run -m unittest discover -s tests -v
python -m coverage report --fail-under=95
```

Tests use the standard library's `unittest`. No database service or API credentials are needed.
HTTP tests bind an ephemeral loopback port; one integration test creates and kills its own child
process. Run the tests where local sockets and child processes are permitted.

## Inspector checks

Node 22+ is needed only for frontend development and browser tests; users running the inspector
need only Python. With the Python virtual environment active:

```bash
npm ci --ignore-scripts
npx playwright install chromium
npm run format:ui
npm run check:ui
npm run test:ui
```

The browser suite creates a temporary database with real workflows and starts its own loopback
server. It checks the retry timeline, search, failure states, HTML escaping, mobile overflow,
connection recovery, and keyboard focus during polling. No production database is used.

## Before changing execution semantics

Read [the architecture](docs/architecture.md). Describe the concrete failure or workflow that
motivates the change. Add a test at the failure boundary, not just an assertion that duplicates
the implementation. Preserve atomic state/event commits and worker fencing. Changes to schema,
checkpoint compatibility, retries, or cancellation should include a short design note.

Keep external runtime dependencies exceptional: propose the need and tradeoff first. New task
integrations usually belong in `examples/` before becoming an engine abstraction. Do not add
claims of exactly-once external execution or production readiness without a precise contract.

## Pull requests

1. Keep one coherent change per PR and explain the before/after behavior.
2. Include relevant tests and documentation; the CI matrix must pass.
3. Run `ruff format .` and `ruff check .` before pushing.
4. For inspector changes, check desktop and narrow/mobile layouts, keyboard navigation,
   empty and failed states, output escaping, and polling behavior. Include a screenshot.
5. For packaging changes, run `python -m build` and `python scripts/smoke_wheel.py`.

Do not include run databases, credentials, real customer payloads, or generated environments.
Bug reports should include Python/OS versions, a minimal workflow, and sanitized event history.

## Finding work

See [the roadmap](docs/roadmap.md) for starter tasks and larger design topics. If proposing a large
feature, open an issue describing the user problem and failure semantics before implementing it.
Treat other contributors with respect, focus review on the code, and assume good intent.

## First contributions

Pick a [good first issue](https://github.com/Sanjays2402/retrace/labels/good%20first%20issue).
Each includes a starting file and an acceptance criterion. Comment with your proposed
approach before a large change; small documentation fixes can go directly to a pull request.
The [getting-started guide](docs/getting-started.md) and [integration examples](docs/examples.md)
are good places to learn the user experience before changing scheduler internals.
