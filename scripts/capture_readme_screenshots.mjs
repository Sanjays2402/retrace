// Capture the four themes and three interactive states from GitHub Pages.
// Run from the repository root: node scripts/capture_readme_screenshots.mjs
import { chromium } from "@playwright/test";
import { resolve } from "node:path";

const site = "https://sanjays2402.github.io/retrace/";
const themes = [
  { accent: "green", mode: "dark" },
  { accent: "red", mode: "light" },
  { accent: "yellow", mode: "light" },
  { accent: "blue", mode: "dark" },
];

const browser = await chromium.launch({ headless: true });
try {
  for (const { accent, mode } of themes) {
    const context = await browser.newContext({
      viewport: { width: 1512, height: 850 },
      deviceScaleFactor: 1,
      colorScheme: mode,
      reducedMotion: "reduce",
    });
    try {
      const page = await context.newPage();
      await page.goto(site, { waitUntil: "networkidle" });
      await page
        .getByRole("button", { name: `${accent} theme`, exact: false })
        .click();
      await page.waitForFunction(
        ({ accent, mode }) =>
          document.documentElement.dataset.accent === accent &&
          document.documentElement.dataset.theme === mode,
        { accent, mode },
      );
      await page.evaluate(() => document.fonts.ready);
      await page.screenshot({
        path: resolve(`docs/assets/theme-${accent}.png`),
        animations: "disabled",
      });
    } finally {
      await context.close();
    }
  }

  const context = await browser.newContext({
    viewport: { width: 1512, height: 1024 },
    deviceScaleFactor: 1,
    colorScheme: "dark",
    reducedMotion: "reduce",
  });
  try {
    const page = await context.newPage();
    await page.goto(site, { waitUntil: "networkidle" });
    const lab = page.locator("#lab");
    await lab
      .getByRole("button", { name: "Go to event 5:", exact: false })
      .click();
    await lab.locator(".node").nth(2).click();
    await lab.screenshot({
      path: resolve("docs/assets/feature-interrupted.png"),
      animations: "disabled",
    });

    await page.getByRole("button", { name: "Blue theme" }).click();
    await lab
      .getByRole("button", { name: "Go to event 8:", exact: false })
      .click();
    await lab.locator(".node").first().click();
    await lab.screenshot({
      path: resolve("docs/assets/feature-recovered.png"),
      animations: "disabled",
    });

    await page.getByRole("button", { name: "Yellow theme" }).click();
    await page.getByRole("button", { name: "Switch to light theme" }).click();
    const signals = page.locator("#signals");
    await signals.getByRole("button", { name: "Start run" }).click();
    await signals.screenshot({
      path: resolve("docs/assets/feature-signal-wait.png"),
      animations: "disabled",
    });
  } finally {
    await context.close();
  }
} finally {
  await browser.close();
}
