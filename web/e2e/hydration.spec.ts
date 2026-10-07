import { expect, type Page, test } from "@playwright/test";

import { LIVE_API } from "../playwright.config";

// Hydration checks in a clean Playwright Chromium (no browser extensions).
// React reports attribute mismatches only in development, so run with the dev server:
//   E2E_DEV=1 npx playwright test e2e/hydration.spec.ts
// In production mode the same assertions still catch runtime/console errors.
test.skip(Boolean(LIVE_API), "uses the mocked API");

const HYDRATION = /hydrat|did(?:n't| not) match|server rendered html|minified react error #(?:418|423|425)/i;

async function mockApi(page: Page) {
  await page.route("**/api/health", (route) =>
    route.fulfill({ json: { status: "ok", provider: "openai", chat_model: "m", embed_model: "e", chunks: 190, reranker: "flashrank", gate_calibrated: true } }),
  );
  await page.route("**/api/eval/latest", (route) => route.fulfill({ json: { run_id: "r", summary: {}, targets: [], failures: [] } }));
  await page.route("**/api/eval/runs", (route) => route.fulfill({ json: { runs: [] } }));
  await page.route("**/api/docs/list", (route) => route.fulfill({ json: { documents: [] } }));
}

/** Collect every console error and uncaught page error during load + hydration. */
async function loadAndCollect(page: Page, path: string): Promise<string[]> {
  const errors: string[] = [];
  page.on("console", (m) => {
    if (m.type() === "error" || m.type() === "warning") errors.push(`[console.${m.type()}] ${m.text()}`);
  });
  page.on("pageerror", (e) => errors.push(`[pageerror] ${e.message}`));
  await page.goto(path);
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(1500); // let React finish hydrating and flush dev warnings
  return errors.filter((e) => !/Download the React DevTools|\[HMR\]|\[Fast Refresh\]/.test(e));
}

for (const scheme of ["light", "dark"] as const) {
  for (const path of ["/", "/eval", "/about"]) {
    test(`no hydration or console errors: ${path} (${scheme})`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: scheme });
      await mockApi(page);
      const errors = await loadAndCollect(page, path);
      expect(errors, errors.join("\n")).toEqual([]);
      // the pre-paint theme script applied the scheme without React complaining
      expect(await page.evaluate(() => document.documentElement.classList.contains("dark"))).toBe(scheme === "dark");
    });
  }
}

test("no hydration errors with a saved theme and a restored conversation", async ({ page }) => {
  await mockApi(page);
  await page.addInitScript(() => {
    localStorage.setItem("finbase.theme", "dark");
    sessionStorage.setItem(
      "finbase.chat.v1",
      JSON.stringify([{ id: "u1", role: "user", content: "Saved question?", createdAt: 1767225600000, status: "done" }]),
    );
  });
  const errors = await loadAndCollect(page, "/");
  expect(errors, errors.join("\n")).toEqual([]);
  await expect(page.getByText("Saved question?")).toBeVisible();
  await expect(page.getByRole("button", { name: "Switch to light mode" })).toBeVisible(); // theme synced after hydration
});

test("control: a Dark Reader-style extension mutating SVGs before hydration IS detected", async ({ page }) => {
  test.skip(!process.env.E2E_DEV, "React reports attribute mismatches only in development builds");
  await mockApi(page);
  // Reproduces what Dark Reader does: add attributes/inline style to SVG icons as soon as they appear,
  // i.e. after the server HTML is parsed but before React hydrates it.
  await page.addInitScript(() => {
    const mark = (el: Element) => {
      if (el instanceof SVGElement && !el.hasAttribute("data-darkreader-inline-stroke")) {
        el.setAttribute("data-darkreader-inline-stroke", "");
        el.setAttribute("style", "--darkreader-inline-stroke: currentColor;");
      }
    };
    new MutationObserver((records) => {
      for (const r of records) r.addedNodes.forEach((n) => n instanceof Element && [n, ...n.querySelectorAll("svg")].forEach(mark));
    }).observe(document, { childList: true, subtree: true });
  });
  const errors = await loadAndCollect(page, "/");
  expect(errors.some((e) => HYDRATION.test(e) && /darkreader/i.test(e)), errors.join("\n")).toBe(true);
});
