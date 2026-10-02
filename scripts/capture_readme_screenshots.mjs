// Run with the browser fixture listening on 127.0.0.1:7762.
// PYTHONPATH=src python scripts/serve_browser_fixture.py
import { chromium } from "@playwright/test";
import { resolve } from "node:path";

const browser = await chromium.launch({ headless: true });
try {
  const context = await browser.newContext({
    viewport: { width: 1512, height: 1024 },
    deviceScaleFactor: 1,
    colorScheme: "dark",
    reducedMotion: "reduce",
  });

  const demo = await context.newPage();
  await demo.goto("https://sanjays2402.github.io/retrace/", {
    waitUntil: "networkidle",
  });
  await demo.locator("#lab .node").first().waitFor();
  await demo.locator("#lab").scrollIntoViewIfNeeded();
  await demo.getByRole("button", { name: "Next event" }).click();
  await demo.getByRole("button", { name: "Next event" }).click();
  await demo.getByRole("button", { name: "Next event" }).click();
  await demo.getByRole("button", { name: "Next event" }).click();
  await demo.locator("#lab .node").nth(2).click();
  await demo.locator("#lab").screenshot({
    path: resolve("docs/assets/recovery-playground.png"),
    animations: "disabled",
  });

  const inspector = await context.newPage();
  await inspector.goto("http://127.0.0.1:7762/", {
    waitUntil: "networkidle",
  });
  await inspector.locator('[data-task="embed"]').click();
  await inspector.locator("#filter-step").click();
  await inspector.locator("#event-kind").selectOption("task.retrying");
  await inspector.locator("#event-query").fill("rate limit");
  await inspector.locator(".event summary").click();
  await inspector.screenshot({
    path: resolve("docs/assets/inspector.png"),
    fullPage: true,
    animations: "disabled",
  });
} finally {
  await browser.close();
}
