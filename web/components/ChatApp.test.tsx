import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const API = "http://api.test";

const done = {
  request_id: "r1",
  answer: "It is 3% [1].",
  formatted: "Answer: It is 3% [1].",
  answerable: true,
  sources: [],
  related_sources: [],
  confidence: { score: 0.8, label: "High" },
  verification: { citations_valid: [1], invalid_markers: [], citation_coverage: 1, unverified_figures: [], repaired_truncations: [], warnings: [] },
  usage: { input_tokens: 1, output_tokens: 1, cost_usd: 0, latency_ms: {} },
  rewritten_query: null,
  language: "en",
  notices: { pii: false, injection: false },
  conflicts: [],
  cached: false,
  degraded: false,
};

function sseResponse(frames: string): Response {
  const bytes = new TextEncoder().encode(frames);
  return new Response(new ReadableStream({ start: (c) => (c.enqueue(bytes), c.close()) }), { headers: { "content-type": "text/event-stream" } });
}

const frame = (event: string, data: unknown) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;

describe("ChatApp end-to-end (mocked fetch)", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.stubEnv("NEXT_PUBLIC_API_URL", API);
    window.sessionStorage.clear();
    Element.prototype.scrollIntoView = vi.fn();
  });
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("streams an answer, then sends history on the follow-up", async () => {
    const bodies: unknown[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        if (url.endsWith("/api/health")) return new Response(JSON.stringify({ status: "ok" }), { headers: { "content-type": "application/json" } });
        bodies.push(JSON.parse(String(init?.body)));
        return sseResponse(frame("meta", { request_id: "r1", rewritten_query: null, language: "en", notices: { pii: false, injection: false } }) + frame("token", "It is ") + frame("token", "3% [1].") + frame("done", done));
      }),
    );
    const { ChatApp } = await import("./ChatApp");
    render(<ChatApp />);
    await userEvent.click(screen.getByRole("button", { name: /foreclosure charge if I close my personal loan/i }));
    await waitFor(() => expect(screen.getByText("High confidence")).toBeInTheDocument());
    expect(screen.getByText(/It is 3%/)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText(/ask a question/i), "and after 24 months?{Enter}");
    await waitFor(() => expect(bodies).toHaveLength(2));
    expect((bodies[1] as { history: unknown[] }).history).toHaveLength(2);
    expect((bodies[0] as { stream: boolean }).stream).toBe(true);
    expect(JSON.parse(window.sessionStorage.getItem("finbase.chat.v1") ?? "[]").length).toBeGreaterThanOrEqual(2);
  });

  it("restores a saved conversation under StrictMode without overwriting it", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ status: "ok" }), { headers: { "content-type": "application/json" } })));
    const saved = [{ id: "u1", role: "user", content: "Saved question?", createdAt: 0, status: "done" }];
    window.sessionStorage.setItem("finbase.chat.v1", JSON.stringify(saved));
    const { StrictMode } = await import("react");
    const { ChatApp } = await import("./ChatApp");
    render(
      <StrictMode>
        <ChatApp />
      </StrictMode>,
    );
    expect(await screen.findByText("Saved question?")).toBeInTheDocument();
    expect(JSON.parse(window.sessionStorage.getItem("finbase.chat.v1") ?? "[]")).toEqual(saved);
  });

  it("shows the configuration banner when NEXT_PUBLIC_API_URL is unset", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "");
    const { ChatApp } = await import("./ChatApp");
    render(<ChatApp />);
    expect(screen.getByText(/API URL is not configured/)).toBeInTheDocument();
  });
});
