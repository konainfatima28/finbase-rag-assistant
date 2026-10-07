import { expect, test } from "@playwright/test";

import { LIVE_API } from "../playwright.config";

// Real end-to-end check (real FastAPI + OpenAI). Run: E2E_LIVE_API=http://127.0.0.1:8000 npx playwright test e2e/live.spec.ts
test.skip(!LIVE_API, "set E2E_LIVE_API to run against a real API");
test.setTimeout(120_000);

test("real API: grounded answer with structural source, follow-up, and abstention", async ({ page }) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (e) => pageErrors.push(e.message));
  await page.goto("/");
  await page.getByRole("button", { name: /foreclosure charge if I close my personal loan/ }).click();
  const chip = page.getByTestId("source-chip").first();
  await expect(chip).toContainText("Personal Loans — Section", { timeout: 60_000 });
  await expect(page.getByText(/3%/).first()).toBeVisible();
  await expect(page.getByText(/(High|Medium) confidence/).first()).toBeVisible();

  await page.getByLabel(/ask a question/i).fill("What if I close it after 2 years instead?");
  await page.keyboard.press("Enter");
  await expect(page.getByText(/1\.5%/).first()).toBeVisible({ timeout: 60_000 });

  await page.getByLabel(/ask a question/i).fill("What is the FinBase home loan interest rate?");
  await page.keyboard.press("Enter");
  await expect(page.getByText("Not in the knowledge base", { exact: true })).toBeVisible({ timeout: 60_000 });
  expect(pageErrors).toEqual([]);
});
