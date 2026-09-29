# Retrace recovery lab

A browser-only walkthrough of Retrace's crash recovery model. It is a synthetic
simulation, not a hosted Python engine. No user data is uploaded or persisted.

## Run locally

Requires Node.js 22.13+ and npm.

```sh
npm ci
npm run dev
```

## Validate

```sh
npm run check
npm run lint
npm test
npm run build:pages
```

The static GitHub Pages output is `dist/pages`. To inspect it locally:

```sh
npx vite preview --config vite.pages.config.ts --host 127.0.0.1
```

Open `http://127.0.0.1:4173/retrace/`.

## Behavior

- Advance one event, jump along the event rail, autoplay to the crash, resume, or replay.
- Inspect successful checkpoints and the interrupted/retried attempt history.
- Filter the event journal to a selected step.
- Preview, copy, or download a synthetic JSON snapshot. Uncommitted outputs are null.
- Follow a worker handoff diagram and try the durable-signals lab: start a run, deliver an
  approval before or after it waits, then resume a worker. The browser does not enqueue runs.
- Optional WebMCP control uses the same validated model. Visible controls work
  without that API.

`lib/recovery.ts` and `lib/signals.ts` contain the simulation models. `app/page.tsx` provides the
interaction, while `app/globals.css` handles responsive layout and reduced motion.
The Python engine and local inspector remain separate from this public demo.

The site is exported as static files; server build intermediates are not deployed.
The generated starter dependencies remain pinned in package-lock.json. Review
package audits before reusing its server packages for server-backed features.

## GitHub Pages

The repository's `Deploy GitHub Pages` workflow tests and builds this app on
changes to `playground/` in `main`, then publishes the static output. It can also
be run manually from Actions. GitHub Pages must use the **GitHub Actions** source.

The build reads `NEXT_PUBLIC_BASE_PATH` from GitHub Pages configuration so asset
URLs work under the repository path. The Pages build defaults to `/retrace`; override
`NEXT_PUBLIC_BASE_PATH` for a different deployment prefix. To reproduce the project-site build:

```sh
NEXT_PUBLIC_BASE_PATH=/retrace npm run build:pages
```

The dedicated Vite entry (`pages.tsx`) shares the recovery UI and model with the
root-hosted demo. Pages output is `dist/pages`; the build verifies local assets
exist beneath the configured URL prefix. This static site requires JavaScript.
