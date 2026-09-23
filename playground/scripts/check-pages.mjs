import assert from "node:assert/strict";
import { readFile, access } from "node:fs/promises";
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
