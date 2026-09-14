import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/browser",
  workers: 1,
  use: {
    baseURL: "http://127.0.0.1:7762",
    viewport: { width: 1512, height: 1100 },
    trace: "retain-on-failure",
    launchOptions: process.env.CHROMIUM_PATH
      ? { executablePath: process.env.CHROMIUM_PATH }
      : {},
  },
  webServer: {
    command: "python scripts/serve_browser_fixture.py",
    url: "http://127.0.0.1:7762",
    reuseExistingServer: false,
    timeout: 30000,
  },
});
