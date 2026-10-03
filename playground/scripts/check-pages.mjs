import assert from "node:assert/strict";
import { readFile, access, readdir } from "node:fs/promises";
import { join } from "node:path";

const base = `${process.env.NEXT_PUBLIC_BASE_PATH ?? "/retrace"}/`;
const html = await readFile("dist/pages/index.html", "utf8");
const urls = [...html.matchAll(/(?:src|href)="([^"#]+)"/g)]
  .map((match) => match[1])
  .filter((url) => !/^https?:/.test(url));
assert(
  urls.some((url) => url.endsWith(".js")),
  "Missing JavaScript entry",
);
assert(
  urls.some((url) => url.endsWith(".css")),
  "Missing stylesheet",
);
for (const url of urls) {
  assert(url.startsWith(base), `Asset outside Pages base path: ${url}`);
  await access(join("dist/pages", url.slice(base.length)));
}
console.log(`Verified ${urls.length} static asset references under ${base}`);

const documentation = join("dist/pages", "docs");
const guides = (await readdir(documentation, { withFileTypes: true }))
  .filter((entry) => entry.isDirectory() && entry.name !== "assets")
  .map((entry) => entry.name);
assert(guides.length >= 9, "Expected the public documentation guides");
for (const slug of ["", ...guides]) {
  const route = slug ? `docs/${slug}/` : "docs/";
  const page = await readFile(join("dist/pages", route, "index.html"), "utf8");
  assert(page.includes("<main id=\"main\""), `Missing readable page: ${route}`);
  for (const [, target] of page.matchAll(/(?:href|src)="([^"]+)"/g)) {
    if (/^(?:https?:|mailto:|#)/.test(target)) continue;
    const resolved = new URL(target, `https://example.test${base}${route}`);
    assert(resolved.pathname.startsWith(base), `Link escapes Pages base: ${target}`);
    const path = resolved.pathname.slice(base.length);
    await access(join("dist/pages", path, path.endsWith("/") ? "index.html" : ""));
  }
}
console.log(`Verified ${guides.length + 1} documentation pages and local links`);
