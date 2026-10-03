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
  await expect(page.locator(".run-card")).toHaveCount(2);
  await page.locator(".run-card").filter({ hasText: "failed-run" }).click();
  await expect(page.locator("#run-status")).toHaveText("failed");
  await expect(page.locator("#task-output")).toContainText("fixture failure");
  expect(errors).toEqual([]);
});

test("waiting signals are visible without a running attempt", async ({
  page,
}) => {
  await page.goto("/");
  await page.locator(".run-card").filter({ hasText: "waiting-run" }).click();
  await expect(page.locator("#run-status")).toHaveText("waiting");
  await expect(page.locator(".node.waiting")).toHaveCount(1);
  await expect(page.locator("#task-output")).toContainText(
    "Waiting for signal: go",
  );
  await expect(page.locator("#task-meta")).toContainText("0 attempts");
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

test("graph zoom survives polling and fit adapts to mobile", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.locator(".node")).toHaveCount(8);
  await page.getByRole("button", { name: "Zoom in", exact: true }).click();
  await expect(page.locator("#zoom-level")).toHaveText("125%");
  await page.waitForTimeout(1200);
  await expect(page.locator("#zoom-level")).toHaveText("125%");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "Fit graph", exact: true }).click();
  await expect
    .poll(() =>
      page
        .locator(".graph-scroll")
        .evaluate((el) => el.scrollWidth - el.clientWidth),
    )
    .toBeLessThanOrEqual(1);
  await page.getByRole("button", { name: "Reset zoom", exact: true }).click();
  await expect(page.locator("#zoom-level")).toHaveText("100%");
  await page.locator('[data-task="report"]').click();
  await expect(page.locator("#task-output")).toContainText("512");
});

test("trace download includes the selected run and all retry attempts", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.locator(".node")).toHaveCount(8);
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("link", { name: "Download trace" }).click();
  const download = await downloadPromise;
  const stream = await download.createReadStream();
  const chunks = [];
  for await (const chunk of stream) chunks.push(chunk);
  const trace = JSON.parse(Buffer.concat(chunks).toString());
  expect(trace.traceEvents.filter((e) => e.ph === "X")).toHaveLength(9);
  expect(
    trace.traceEvents.filter((e) => e.args.status === "failed"),
  ).toHaveLength(1);
  expect(download.suggestedFilename()).toContain(trace.retrace.run_id);
  await page.locator(".run-card").filter({ hasText: "html-output" }).click();
  await expect(page.locator("#download-trace")).not.toHaveAttribute(
    "href",
    `/api/runs/${trace.retrace.run_id}/trace`,
  );
});

test("permalinks restore step and timeline across reload and browser history", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.locator(".node")).toHaveCount(8);
  await page.locator('[data-task="report"]').click();
  await page.getByRole("tab", { name: "Attempt timeline" }).click();
  await expect(page).toHaveURL(/task=report&view=timeline/);
  await page.reload();
  await expect(page.locator("#task-output")).toContainText("512");
  await expect(
    page.getByRole("tab", { name: "Attempt timeline" }),
  ).toHaveAttribute("aria-selected", "true");
  await page.locator(".run-card").filter({ hasText: "html-output" }).click();
  await expect(page.locator("#workflow-name")).toHaveText("html-output");
  await page.goBack();
  await expect(page.locator("#workflow-name")).toHaveText("document-indexing");
  await expect(page.locator("#task-output")).toContainText("512");
  await page.goForward();
  await expect(page.locator("#workflow-name")).toHaveText("html-output");
});

test("worker epochs visualize retry handoff and survive reload", async ({
  page,
}) => {
  await page.goto("/");
  await page.locator(".run-card").filter({ hasText: "recovered-run" }).click();
  await page.getByRole("tab", { name: "Worker epochs" }).click();
  await expect(page.locator(".epoch-card")).toHaveCount(2);
  await expect(page.locator(".epoch-card.failed")).toContainText("EPOCH 01");
  await expect(page.locator(".epoch-card.completed")).toContainText("EPOCH 02");
  await page.screenshot({
    path: test.info().outputPath("worker-epochs.png"),
    fullPage: true,
  });
  await expect(page).toHaveURL(/view=epochs/);
  await page.reload();
  await expect(
    page.getByRole("tab", { name: "Worker epochs" }),
  ).toHaveAttribute("aria-selected", "true");
  await expect(page.locator(".epoch-card")).toHaveCount(2);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});

test("unknown run permalink can recover by selecting an existing run", async ({
  page,
}) => {
  await page.goto("/#run=missing&task=unknown");
  await expect(page.locator("#connection")).toHaveText(
    "Run not found · select another run",
  );
  await page
    .locator(".run-card")
    .filter({ hasText: "document-indexing" })
    .click();
  await expect(page.locator(".node")).toHaveCount(8);
  await expect(page.locator("#connection")).toContainText("Live");
});

test("browse older runs, filter them, and return to the latest page", async ({
  page,
}) => {
  const requests = [];
  await page.route("**/api/runs?limit=*", async (route) => {
    const response = await route.fetch();
    const data = await response.json();
    const limit = Number(
      new URL(route.request().url()).searchParams.get("limit"),
    );
    requests.push(limit);
    const sample = data.runs[0];
    const runs = Array.from({ length: 102 }, (_, i) => ({
      ...sample,
      id: i === 0 ? sample.id : `history-${i}`,
      name: i === 101 ? "older-approval" : sample.name,
      status: i === 101 ? "waiting" : "succeeded",
      created_at: sample.created_at - i,
    }));
    await route.fulfill({ json: { runs: runs.slice(0, limit) } });
  });
  await page.goto("/");
  await expect(page.locator(".run-card")).toHaveCount(100);
  await expect(page.locator(".node")).toHaveCount(8);
  await page.getByRole("button", { name: "Show 100 more" }).click();
  await expect(page.locator(".run-card")).toHaveCount(102);
  await expect(page.locator("#load-runs")).toBeHidden();
  await page.locator("#search").fill("older-approval");
  await page.locator('[data-filter="attention"]').click();
  await expect(page.locator(".run-card")).toHaveCount(1);
  await page.locator("#refresh").click();
  await expect(page.locator(".run-card")).toHaveCount(1);
  await expect(page.locator("#history-summary")).toContainText(
    "102 recent runs loaded",
  );
  await page.getByRole("button", { name: "Latest 100" }).click();
  await expect(page.locator(".run-card")).toHaveCount(0);
  await expect(page.locator("#reset-runs")).toBeHidden();
  expect(requests).toContain(200);
});

test("expanded history is bounded at one thousand runs", async ({ page }) => {
  await page.route("**/api/runs?limit=*", async (route) => {
    const response = await route.fetch();
    const sample = (await response.json()).runs[0];
    const limit = Number(
      new URL(route.request().url()).searchParams.get("limit"),
    );
    await route.fulfill({
      json: {
        runs: Array.from({ length: limit }, (_, i) => ({
          ...sample,
          id: i === 0 ? sample.id : `bounded-${i}`,
        })),
      },
    });
  });
  await page.goto("/");
  for (let count = 200; count <= 1000; count += 100) {
    await page.getByRole("button", { name: "Show 100 more" }).click();
    await expect(page.locator(".run-card")).toHaveCount(count);
  }
  await expect(page.locator("#load-runs")).toBeHidden();
  await expect(page.locator("#history-summary")).toContainText(
    "retrace runs --before",
  );
});

test("history changes made during a slow poll are fetched afterward", async ({
  page,
}) => {
  let release;
  const blocked = new Promise((resolve) => {
    release = resolve;
  });
  let signal;
  const polling = new Promise((resolve) => {
    signal = resolve;
  });
  let calls = 0;
  await page.route("**/api/runs?limit=*", async (route) => {
    const response = await route.fetch();
    const sample = (await response.json()).runs[0];
    const limit = Number(
      new URL(route.request().url()).searchParams.get("limit"),
    );
    if (++calls === 2) {
      signal();
      await blocked;
    }
    await route.fulfill({
      json: {
        runs: Array.from({ length: limit }, (_, i) => ({
          ...sample,
          id: i === 0 ? sample.id : `slow-${i}`,
        })),
      },
    });
  });
  await page.goto("/");
  await expect(page.locator(".run-card")).toHaveCount(100);
  await polling;
  await page.getByRole("button", { name: "Show 100 more" }).click();
  await expect(page.locator("#load-runs")).toBeDisabled();
  release();
  await expect(page.locator(".run-card")).toHaveCount(200);
  await expect(page.locator("#load-runs")).toBeEnabled();
});
