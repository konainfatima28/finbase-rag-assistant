import { expect, test } from "@playwright/test";

import { LIVE_API, MOCK_API } from "../playwright.config";

test.skip(Boolean(LIVE_API), "mocked-API tests are skipped when E2E_LIVE_API is set");

const result = {
  request_id: "req-1",
  answer: "Closing before 24 months costs 3% of the outstanding principal [1].",
  formatted: "Answer: Closing before 24 months costs 3% of the outstanding principal [1].",
  answerable: true,
  sources: [
    {
      n: 1,
      chunk_id: "c1",
      doc_id: "personal_loans",
      doc_title: "FinBase Personal Loans Master Policy & Operational Manual",
      doc_code: "FB-POL-PL-2026-V4",
      section_id: "6.2",
      section_title: "Foreclosure Charges & Rules",
      page_start: 3,
      page_end: 3,
      chunk_type: "policy",
      citation: "FinBase Personal Loans Master Policy & Operational Manual — Section 6.2 (p. 3)",
      snippet: "The applicable foreclosure charge is 3% of the outstanding principal",
      relevance: 0.91,
      role: "primary",
      faq_id: null,
      quality_flag: "ok",
    },
  ],
  related_sources: [],
  confidence: { score: 0.8, label: "High" },
  verification: { citations_valid: [1], invalid_markers: [], citation_coverage: 1, unverified_figures: [], repaired_truncations: [], warnings: [] },
  usage: { input_tokens: 1, output_tokens: 1, cost_usd: 0, latency_ms: { total: 1 } },
  rewritten_query: null,
  language: "en",
  notices: { pii: false, injection: false },
  conflicts: [],
  cached: false,
  degraded: false,
};

const frame = (event: string, data: unknown) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;

test("ask a starter question and open its source (mocked API)", async ({ page }) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (e) => pageErrors.push(e.message));
  await page.route(`${MOCK_API}/api/health`, (route) => route.fulfill({ json: { status: "ok", provider: "openai", chat_model: "m", embed_model: "e", chunks: 190, reranker: "flashrank", gate_calibrated: true } }));
  await page.route(`${MOCK_API}/api/chat`, (route) =>
    route.fulfill({
      status: 200,
      headers: { "content-type": "text/event-stream", "cache-control": "no-cache" },
      body:
        frame("meta", { request_id: "req-1", rewritten_query: null, language: "en", notices: { pii: false, injection: false } }) +
        frame("token", "Closing before 24 months costs 3% ") +
        frame("token", "of the outstanding principal [1].") +
        frame("sources", { sources: result.sources, related_sources: [] }) +
        frame("verification", result.verification) +
        frame("done", result),
    }),
  );
  await page.route(`${MOCK_API}/api/chunks/c1`, (route) =>
    route.fulfill({ json: { ...result.sources[0], breadcrumb: "Loans › Section 6 › 6.2", text: "• The applicable foreclosure charge is 3% of the outstanding principal if closed before 24 months.", source_duplicates: [] } }),
  );

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "How can we help today?" })).toBeVisible();
  await page.getByRole("button", { name: /foreclosure charge if I close my personal loan/ }).click();

  await expect(page.getByText("Closing before 24 months costs 3% of the outstanding principal")).toBeVisible();
  await expect(page.getByText("High confidence")).toBeVisible();
  const chip = page.getByTestId("source-chip");
  await expect(chip).toContainText("Section 6.2: Foreclosure Charges & Rules");
  // an inline citation opens the evidence drawer directly
  await page.getByRole("button", { name: /^Source 1:/ }).first().click();
  await expect(page.getByRole("dialog")).toContainText("Loans › Section 6 › 6.2");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toBeHidden();
  // so does the source chip
  await chip.click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toBeHidden();
  await expect(page.getByText(/Not financial advice/)).toBeVisible();
  expect(pageErrors).toEqual([]);
});

test("shows the waking banner while the API is down", async ({ page }) => {
  await page.route(`${MOCK_API}/api/health`, (route) => route.abort());
  await page.goto("/");
  await expect(page.getByText(/The API is waking up/)).toBeVisible({ timeout: 20_000 });
});
