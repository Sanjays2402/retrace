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
npm test
npm run build
```

The static output is `dist/client`. To inspect the production build locally:

```sh
python3 -m http.server 3000 --bind 127.0.0.1 --directory dist/client
```

## Behavior

- Advance one event, autoplay to the crash, resume, or replay.
- Inspect successful checkpoints and the interrupted/retried attempt history.
- Filter the event journal to a selected step.
- Preview, copy, or download a synthetic JSON snapshot. Uncommitted outputs are null.
- Optional WebMCP control uses the same validated model. Visible controls work
  without that API.

`lib/recovery.ts` contains the model and validation. `app/page.tsx` provides the
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
URLs work under the repository path. The default is an empty prefix for local
and root-domain hosting. To reproduce the project-site build:

```sh
NEXT_PUBLIC_BASE_PATH=/retrace npm run build:pages
```

The dedicated Vite entry (`pages.tsx`) shares the recovery UI and model with the
root-hosted demo. Pages output is `dist/pages`; the build verifies local assets
exist beneath the configured URL prefix. This static site requires JavaScript.
