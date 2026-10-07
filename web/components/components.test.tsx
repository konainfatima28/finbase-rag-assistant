import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { followUpsFor, historyFrom } from "@/lib/conversation";
import type { ChatMessageView, ChatResult, Source } from "@/lib/types";

import { AnswerCard } from "./AnswerCard";
import { Composer } from "./Composer";
import { RichAnswer } from "./RichAnswer";
import { SourceDrawer } from "./SourceDrawer";
import { SourcesPanel } from "./SourcesPanel";
import { BarList } from "./eval/BarList";

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getChunk: vi.fn(async () => ({ chunk_id: "c1", breadcrumb: "Loans › 6.2", text: "Intro. The applicable foreclosure charge is 3% of the outstanding principal. End.", source_duplicates: [] })),
}));

const source = (n: number, extra: Partial<Source> = {}): Source => ({
  n,
  chunk_id: `c${n}`,
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
  relevance: 0.93,
  role: "primary",
  faq_id: null,
  quality_flag: "ok",
  ...extra,
});

const result = (overrides: Partial<ChatResult> = {}): ChatResult => ({
  request_id: "r1",
  answer: "The charge is **3%** of the outstanding principal [1].\n- Not allowed in the first 6 months [1]",
  formatted: "Answer: …",
  answerable: true,
  sources: [source(1)],
  related_sources: [],
  confidence: { score: 0.82, label: "High" },
  verification: { citations_valid: [1], invalid_markers: [], citation_coverage: 1, unverified_figures: [], repaired_truncations: [], warnings: [] },
  usage: { input_tokens: 10, output_tokens: 5, cost_usd: 0.0001, latency_ms: { total: 900 } },
  rewritten_query: null,
  language: "en",
  notices: { pii: false, injection: false },
  conflicts: [],
  cached: false,
  degraded: false,
  ...overrides,
});

const message = (r: ChatResult | undefined, status: ChatMessageView["status"] = "done"): ChatMessageView => ({
  id: "m1",
  role: "assistant",
  content: r?.answer ?? "",
  createdAt: Date.UTC(2026, 9, 6, 10, 30),
  status,
  result: r,
});

describe("RichAnswer (XSS-safe markdown + citation chips)", () => {
  it("renders bold, bullets and clickable chips only for valid citations", async () => {
    const onCite = vi.fn();
    render(<RichAnswer text={"Fee is **3%** [1][9].\n- Item one [2]"} validCitations={[1, 2]} onCite={onCite} />);
    expect(screen.getByText("3%").tagName).toBe("STRONG");
    expect(screen.getByRole("listitem")).toHaveTextContent("Item one");
    expect(screen.queryByRole("button", { name: "Show source 9" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Show source 1" }));
    expect(onCite).toHaveBeenCalledWith(1);
  });

  it("never renders HTML from model text", () => {
    const { container } = render(<RichAnswer text={'<img src=x onerror="alert(1)"> <script>alert(2)</script> ok'} validCitations={[]} onCite={() => {}} />);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect(container).toHaveTextContent("<img src=x onerror=");
  });
});

describe("SourcesPanel (canonical source chips)", () => {
  it("shows one compact chip per source with the canonical label and opens evidence on click", async () => {
    const onView = vi.fn();
    const faq = source(2, { chunk_type: "faq", faq_id: "Q001", role: "secondary", section_id: "23", label: "Personal Loans — FAQ Q001" });
    render(<SourcesPanel sources={[source(1, { label: "Personal Loans — Section 6.2: Foreclosure Charges & Rules" }), faq]} title="Sources" onView={onView} idPrefix="m" />);
    const chips = screen.getAllByTestId("source-chip");
    expect(chips).toHaveLength(2);
    expect(chips[0]).toHaveTextContent("Personal Loans — Section 6.2: Foreclosure Charges & Rules");
    expect(chips[1]).toHaveTextContent("Personal Loans — FAQ Q001");
    await userEvent.click(screen.getByRole("button", { name: /Source 1: Personal Loans — Section 6\.2.*p\. 3/ }));
    expect(onView).toHaveBeenCalledWith(expect.objectContaining({ chunk_id: "c1" }));
  });

  it("builds a label from metadata for older payloads and never shows a percentage", () => {
    render(<SourcesPanel sources={[source(1, { relevance: 0.93 })]} title="Sources" onView={vi.fn()} idPrefix="m" />);
    const chip = screen.getByTestId("source-chip");
    expect(chip).toHaveTextContent("FinBase Personal Loans Master Policy & Operational Manual — Section 6.2: Foreclosure Charges & Rules");
    expect(chip.textContent).not.toMatch(/%/);
  });

  it("marks unclear and conflicting sources accessibly", () => {
    render(
      <SourcesPanel
        sources={[source(1, { status: "unclear_value" }), source(2, { status: "conflicting_sources", chunk_id: "c2" }), source(3, { status: "normal", chunk_id: "c3" })]}
        title="Sources"
        onView={vi.fn()}
        idPrefix="m"
      />,
    );
    expect(screen.getByText("(incomplete value)")).toBeInTheDocument();
    expect(screen.getByText("(conflicting value)")).toBeInTheDocument();
    expect(screen.getAllByTestId("source-chip")[2]!.textContent).not.toMatch(/incomplete|conflicting/);
  });
});

describe("AnswerCard", () => {
  const handlers = () => ({ question: "q", onAsk: vi.fn(), onFeedback: vi.fn(), onViewSource: vi.fn() });

  it("answer first, then confidence, source chips, follow-ups and feedback controls", async () => {
    const h = handlers();
    render(<AnswerCard message={message(result())} {...h} />);
    expect(screen.getByText("High confidence")).toBeInTheDocument();
    expect(screen.getAllByTestId("source-chip")).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: /\?$/ }).length).toBeGreaterThanOrEqual(2);
    expect(screen.getByRole("region", { name: "Sources" }).textContent).not.toMatch(/%/);
    await userEvent.click(screen.getByRole("button", { name: "Helpful" }));
    expect(h.onFeedback).toHaveBeenCalledWith(expect.anything(), "up");
  });

  it("an inline citation opens the evidence for that source", () => {
    const h = handlers();
    render(<AnswerCard message={message(result({ sources: [source(1, { label: "Personal Loans — Section 6.2: Foreclosure Charges & Rules" })] }))} {...h} />);
    fireEvent.click(screen.getAllByRole("button", { name: "Source 1: Personal Loans — Section 6.2: Foreclosure Charges & Rules" })[0]!);
    expect(h.onViewSource).toHaveBeenCalledWith(expect.objectContaining({ n: 1 }));
  });

  it("hides citation markers while streaming (numbers are final only when done)", () => {
    render(<AnswerCard message={{ ...message(undefined, "streaming"), content: "It is 3% [4]." }} {...handlers()} />);
    expect(screen.getByText(/It is 3%/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /source 4/i })).toBeNull();
    expect(screen.queryByText("[4]")).toBeNull();
  });

  it("explains conflicts and incomplete values from the evidence status", () => {
    const r = result({
      verification: { ...result().verification, unverified_figures: ["0%"], warnings: ["unverified_figures", "conflicting_sources", "unclear_value"] },
      evidence: {
        statuses: ["unclear_value", "conflicting_sources"],
        items: [],
        claims: [],
        unclear_values: [{ chunk_id: "c", line: 1, column: "Daily Limit", row: "Contactless Tap", evidence_id: "savings_account:section:4", n: 1 }],
        conflicts: [
          {
            conflict_id: "k",
            status: "conflicting_sources",
            category: "range_conflict",
            doc_id: "personal_loans",
            doc_title: "FinBase Personal Loans Master Policy & Operational Manual",
            scope: "same_document",
            same_document: true,
            members: ["21", "4.2"],
            sections: ["Section 21", "Section 4.2"],
            values: ["₹150 - ₹350", "₹150 to ₹400"],
            evidence_ids: ["a", "b"],
            citations: [1, 2],
            description: "Section 21 and Section 4.2 of the FinBase Personal Loans Master Policy & Operational Manual state different values.",
          },
        ],
      },
    });
    render(<AnswerCard message={message(r)} {...handlers()} />);
    expect(screen.getByText("Some figures could not be verified")).toBeInTheDocument();
    expect(screen.getByText(/Section 21 and Section 4\.2 of the FinBase Personal Loans/)).toHaveTextContent("state different values: ₹150 - ₹350 vs ₹150 to ₹400. Please confirm");
    expect(screen.getByText(/The Daily Limit for Contactless Tap is truncated/)).toBeInTheDocument();
  });

  it("falls back to the conflict badge for payloads without evidence", () => {
    const r = result({ verification: { ...result().verification, warnings: ["conflicting_sources"] } });
    render(<AnswerCard message={message(r)} {...handlers()} />);
    expect(screen.getByText(/Documents differ/)).toBeInTheDocument();
  });

  it("shows one clear not-in-knowledge-base state, with related topics only when sent", () => {
    const text = "I couldn't find this in FinBase's documents. Please contact FinBase support at support@finbase.com.";
    const r = result({ answerable: false, answer: text, sources: [], related_sources: [source(0, { role: "related" })], verification: { ...result().verification, citations_valid: [] } });
    const { rerender } = render(<AnswerCard message={message(r)} {...handlers()} />);
    expect(screen.getByText("Not in the knowledge base")).toBeInTheDocument();
    expect(screen.getByText(/support@finbase\.com/)).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Related topics" })).toBeInTheDocument();
    expect(screen.queryByText(/confidence/)).toBeNull();
    rerender(<AnswerCard message={message({ ...r, related_sources: [] })} {...handlers()} />);
    expect(screen.queryByRole("region", { name: "Related topics" })).toBeNull();
  });

  it("shows a loading skeleton while waiting for the first token", () => {
    render(<AnswerCard message={message(undefined, "streaming")} {...handlers()} />);
    expect(screen.getByLabelText("Generating answer")).toBeInTheDocument();
  });
});

describe("SourceDrawer (evidence on demand)", () => {
  it("shows the label, status note and full text with the snippet highlighted; Escape closes and returns focus", async () => {
    const opener = document.createElement("button");
    document.body.appendChild(opener);
    opener.focus();
    const onClose = vi.fn();
    const s = source(1, { label: "Savings Account — Section 4: Telemetry", status: "unclear_value", unclear_rows: [{ chunk_id: "c1", line: 1, column: "Daily Limit", row: "Contactless Tap" }] });
    const { unmount } = render(<SourceDrawer source={s} onClose={onClose} />);
    const dialog = screen.getByRole("dialog", { name: "Savings Account — Section 4: Telemetry" });
    expect(dialog).toHaveTextContent("Incomplete value in this source: Daily Limit for Contactless Tap");
    expect(await screen.findByText("The applicable foreclosure charge is 3% of the outstanding principal")).toHaveProperty("tagName", "MARK");
    expect(screen.getByRole("button", { name: "Close source" })).toHaveFocus();
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalled();
    unmount();
    expect(opener).toHaveFocus();
  });
});

describe("Composer", () => {
  it("submits on Enter, inserts newline on Shift+Enter, and offers Stop while busy", async () => {
    const onSend = vi.fn();
    const { rerender } = render(<Composer busy={false} disabled={false} onSend={onSend} onStop={() => {}} />);
    const box = screen.getByLabelText(/ask a question/i);
    await userEvent.type(box, "line one{Shift>}{Enter}{/Shift}line two");
    expect(box).toHaveValue("line one\nline two");
    await userEvent.type(box, "{Enter}");
    expect(onSend).toHaveBeenCalledWith("line one\nline two");
    const onStop = vi.fn();
    rerender(<Composer busy disabled={false} onSend={onSend} onStop={onStop} />);
    await userEvent.click(screen.getByRole("button", { name: /stop generating/i }));
    expect(onStop).toHaveBeenCalled();
  });

  it("does not send blank input", async () => {
    const onSend = vi.fn();
    render(<Composer busy={false} disabled={false} onSend={onSend} onStop={() => {}} />);
    await userEvent.type(screen.getByLabelText(/ask a question/i), "   {Enter}");
    expect(onSend).not.toHaveBeenCalled();
  });
});

describe("conversation helpers", () => {
  it("builds history from completed turns and template follow-ups", () => {
    const user: ChatMessageView = { id: "u", role: "user", content: "Q?", createdAt: 0, status: "done" };
    const history = historyFrom([user, message(result())]);
    expect(history).toEqual([
      { role: "user", content: "Q?" },
      { role: "assistant", content: result().answer },
    ]);
    expect(followUpsFor([source(1)], "What if I close the loan after 24 months?")).not.toContain("What if I close the loan after 24 months?");
    expect(followUpsFor([], "x")).toEqual([]);
  });
});

describe("BarList", () => {
  it("renders values and an accessible table, with a dash for unmeasured metrics", () => {
    render(<BarList rows={[{ label: "recall@5", value: 0.9 }, { label: "mrr", value: null }]} max={1} />);
    expect(screen.getAllByText("0.900").length).toBeGreaterThan(0);
    expect(screen.getByText("not measured")).toBeInTheDocument();
  });
});
