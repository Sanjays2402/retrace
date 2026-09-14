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
