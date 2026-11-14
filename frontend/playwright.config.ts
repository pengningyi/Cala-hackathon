import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  timeout: 120_000,
  expect: { timeout: 10_000 },
  workers: 1,
  reporter: "line",
  use: {
    baseURL: process.env.FRONTEND_TEST_URL || "http://127.0.0.1:3000",
    channel: process.platform === "win32" ? "msedge" : undefined,
    viewport: { width: 1440, height: 1000 },
    permissions: ["clipboard-read", "clipboard-write"],
    screenshot: "only-on-failure",
  },
});
