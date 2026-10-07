import { defineConfig, devices } from "@playwright/test";

// Smoke test against a MOCKED API (requests to MOCK_API are fulfilled by page.route in the test).
export const MOCK_API = "http://127.0.0.1:3999";
// Set E2E_LIVE_API (e.g. http://127.0.0.1:8000 or the Render URL) to run e2e/live.spec.ts against a real API.
export const LIVE_API = process.env.E2E_LIVE_API ?? "";
// Set E2E_BASE_URL to test an already-running app (e.g. your own `next dev` on :3000) instead of starting one.
const EXISTING = process.env.E2E_BASE_URL ?? "";

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  retries: 0,
  use: { baseURL: EXISTING || "http://127.0.0.1:3100", trace: "retain-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: EXISTING
    ? undefined
    : {
    command: process.env.E2E_DEV ? "npx next dev -p 3100 -H 127.0.0.1" : "npm run build && npx next start -p 3100 -H 127.0.0.1",
    url: "http://127.0.0.1:3100",
    timeout: 240_000,
    reuseExistingServer: false,
    env: { NEXT_PUBLIC_API_URL: LIVE_API || MOCK_API },
  },
});
