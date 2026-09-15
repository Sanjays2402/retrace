import { test, expect } from "@playwright/test";

test("real checkpoints, retries, timeline, search, and filters", async ({
  page,
}) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await expect(page.locator(".node")).toHaveCount(8);
  await page.locator('[data-task="embed"]').click();
  await expect(page.locator("#task-meta")).toContainText("2 attempts");
  await expect(page.locator("#task-output")).toContainText("512");
  await page.getByRole("tab", { name: "Attempt timeline" }).click();
  await expect(page.locator(".attempt")).toHaveCount(9);
  await page.locator("#search").fill("absent");
  await expect(page.locator(".run-card")).toHaveCount(0);
  await page.locator("#search").fill("");
  await page.locator('[data-filter="attention"]').click();
  await expect(page.locator(".run-card")).toHaveCount(1);
  await page.locator(".run-card").click();
  await expect(page.locator("#run-status")).toHaveText("failed");
  await expect(page.locator("#task-output")).toContainText("fixture failure");
  expect(errors).toEqual([]);
});

test("stored output is rendered as text, never executable markup", async ({
  page,
}) => {
  await page.goto("/");
  await page.locator(".run-card").filter({ hasText: "html-output" }).click();
  await expect(page.locator("#task-output")).toContainText("<img src=x");
  await expect(page.locator("#task-output img")).toHaveCount(0);
  expect(await page.evaluate(() => window.pwned)).toBeUndefined();
});

test("mobile layout keeps overflow inside the graph", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(page.locator(".node")).toHaveCount(8);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page
    .locator(".graph-scroll")
    .evaluate((el) => (el.scrollLeft = el.scrollWidth));
  await page.locator('[data-task="report"]').click();
  await expect(page.locator("#task-title")).toHaveText("report");
  await page.screenshot({
    path: test.info().outputPath("journal-mobile.png"),
    fullPage: true,
  });
});

test("connection failures are visible and recover automatically", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.locator(".node")).toHaveCount(8);
  await page.route("**/api/**", (route) => route.abort());
  await page.locator("#refresh").click();
  await expect(page.locator("#connection")).toContainText("Connection lost");
  await page.unroute("**/api/**");
  await expect(page.locator("#connection")).toContainText("Live");
});

test("polling preserves keyboard focus on workflow steps", async ({ page }) => {
  await page.goto("/");
  const step = page.locator('[data-task="embed"]');
  await step.focus();
  await page.waitForResponse(
    (response) => response.url().includes("/events?after=") && response.ok(),
  );
  await expect(step).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("#task-title")).toHaveText("embed");
});

test("journal filters compose, preserve payload expansion, and clear", async ({
  page,
}) => {
  await page.goto("/");
  await page.locator('[data-task="embed"]').click();
  await page.locator("#filter-step").click();
  await expect(page.locator("#event-task")).toHaveValue("embed");
  await expect(page.locator(".event")).toHaveCount(4);
  await page.locator("#event-kind").selectOption("task.retrying");
  await expect(page.locator(".event")).toHaveCount(1);
  await page.locator("#event-query").fill("rate limit");
  await expect(page.locator(".event")).toHaveCount(1);
  await page.locator(".event summary").click();
  await expect(page.locator(".event details")).toHaveAttribute("open", "");
  await page.waitForResponse(
    (response) => response.url().includes("/events?after=") && response.ok(),
  );
  await expect(page.locator(".event details")).toHaveAttribute("open", "");
  await page.screenshot({
    path: test.info().outputPath("journal-desktop.png"),
    fullPage: true,
  });
  await page.locator("#event-query").fill("not-in-the-journal");
  await expect(page.locator(".event")).toHaveCount(0);
  await expect(page.locator("#export-events")).toBeDisabled();
  await page.locator("#clear-events").click();
  await expect(page.locator(".event")).toHaveCount(21);
});

test("JSONL download contains exactly the shown events in ascending order", async ({
  page,
}) => {
  const { readFile } = await import("node:fs/promises");
  await page.goto("/");
  await expect(page.locator(".event")).toHaveCount(21);
  await page.locator("#event-task").selectOption("embed");
  const pending = page.waitForEvent("download");
  await page.locator("#export-events").click();
  const download = await pending;
  const events = (await readFile(await download.path(), "utf8"))
    .trim()
    .split("\n")
    .map(JSON.parse);
  expect(events).toHaveLength(4);
  expect(events.every((event) => event.task_name === "embed")).toBe(true);
  expect(events.map((event) => event.id)).toEqual(
    events.map((event) => event.id).sort((a, b) => a - b),
  );
  expect(events.some((event) => event.kind === "task.retrying")).toBe(true);
});

test("active filters never discard incoming events or change the journal cursor", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.locator(".event")).toHaveCount(21);
  await page.locator("#event-task").selectOption("embed");
  await page.route("**/events?after=*", async (route) => {
    const after = Number(
      new URL(route.request().url()).searchParams.get("after"),
    );
    const response = await route.fetch();
    const body = await response.json();
    if (after < 100000) {
      body.events.push({
        id: 100000,
        run_id: "fixture",
        task_name: "audit",
        kind: "task.note",
        at: 1700000000,
        payload: { message: "arrived while embed was selected" },
      });
      body.cursor = 100000;
    }
    await route.fulfill({ response, json: body });
  });
  await expect(page.locator("#event-count")).toHaveText("4 / 22 shown");
  await expect(page.locator(".event")).toHaveCount(4);
  await page.locator("#clear-events").click();
  await expect(page.locator(".event")).toHaveCount(22);
  await expect(page.locator(".event").first()).toContainText("task.note");
  await page.waitForRequest((request) =>
    request.url().includes("events?after=100000"),
  );
  await expect(page.locator(".event")).toHaveCount(22);
});

test("bounded journal retention keeps the newest events and makes export scope explicit", async ({
  page,
}) => {
  await page.route("**/events?after=*", async (route) => {
    const after = Number(
      new URL(route.request().url()).searchParams.get("after"),
    );
    const end = Math.min(after + 500, 1100);
    const events = Array.from(
      { length: Math.max(0, end - after) },
      (_, index) => ({
        id: after + index + 1,
        run_id: "fixture",
        task_name: "embed",
        kind: "task.note",
        at: 1700000000,
        payload: { message: `event ${after + index + 1}` },
      }),
    );
    await route.fulfill({ json: { events, cursor: end } });
  });
  await page.goto("/");
  await expect(page.locator("#event-count")).toHaveText("1000 / 1000 shown");
  await expect(page.locator(".event").first()).toHaveAttribute(
    "data-event-id",
    "1100",
  );
  await expect(page.locator(".event").last()).toHaveAttribute(
    "data-event-id",
    "101",
  );
  await expect(page.locator("#event-retention")).toContainText("full journal");
});

test("manually recovered runs show lifetime failures and reset payloads", async ({
  page,
}) => {
  await page.goto("/");
  await page.locator(".run-card").filter({ hasText: "recovered-run" }).click();
  await expect(page.locator("#run-status")).toHaveText("succeeded");
  await expect(page.locator("#task-meta")).toContainText(
    "2 attempts · 1 lifetime failure · 0 in current budget",
  );
  await page.locator("#event-kind").selectOption("task.reset");
  await expect(page.locator(".event")).toHaveCount(1);
  await page.locator(".event summary").click();
  await expect(page.locator(".event pre")).toContainText(
    '"previous_failures": 1',
  );
  await page
    .locator(".run-card")
    .filter({ hasText: "document-indexing" })
    .click();
  await expect(page.locator("#event-kind")).toHaveValue("");
  await expect(page.locator(".event")).toHaveCount(21);
});
