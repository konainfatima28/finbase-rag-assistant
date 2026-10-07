// Conversation helpers: history window, sessionStorage persistence, template follow-ups, starters.
import type { ChatMessageView, HistoryTurn, Source } from "./types";

export const STORAGE_KEY = "finbase.chat.v1";
export const HISTORY_TURNS = 6;

/** Last N completed turns sent with every request (the API is stateless across cold starts). */
export function historyFrom(messages: ChatMessageView[], limit = HISTORY_TURNS): HistoryTurn[] {
  return messages
    .filter((m) => m.status === "done" && (m.role === "user" || m.result))
    .map((m) => ({ role: m.role, content: m.role === "assistant" ? (m.result?.answer ?? m.content) : m.content }))
    .slice(-limit);
}

export function loadConversation(): ChatMessageView[] {
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as ChatMessageView[];
    return Array.isArray(parsed) ? parsed.filter((m) => m.status !== "streaming") : [];
  } catch {
    return [];
  }
}

export function saveConversation(messages: ChatMessageView[]): void {
  try {
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(messages.filter((m) => m.status !== "streaming")));
  } catch {
    /* storage full or blocked (private mode): persistence is best-effort */
  }
}

export function clearConversation(): void {
  try {
    window.sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    /* ignore */
  }
}

export const STARTERS: { label: string; question: string }[] = [
  { label: "Personal loans", question: "What is the foreclosure charge if I close my personal loan after 18 months?" },
  { label: "Credit cards", question: "What is the annual fee on the FinBase Luxe card and how can it be waived?" },
  { label: "Savings", question: "How many free ATM withdrawals do I get each month on my savings account?" },
  { label: "Payments & UPI", question: "My UPI payment failed but money was debited. When will I get the refund?" },
  { label: "FDs & wealth", question: "What FD interest rate do senior citizens get for a 1-year deposit?" },
  { label: "KYC & security", question: "What do I need for Video KYC and when are agents available?" },
];

const FOLLOW_UPS: Record<string, string[]> = {
  personal_loans: ["What if I close the loan after 24 months?", "What is the processing fee on a personal loan?", "What happens if an EMI bounces?"],
  credit_cards: ["What is the minimum amount due?", "What is the foreign currency markup on each card?", "How long is the interest-free grace period?"],
  savings_account: ["What interest rate do I earn on my savings balance?", "Is there a minimum balance requirement?", "What does a physical debit card cost?"],
  payments_upi: ["What compensation do I get if the refund is delayed?", "What is the daily UPI limit?", "How do I dispute an unauthorized debit?"],
  fd_wealth: ["What is the penalty for breaking an FD early?", "When is TDS deducted on FD interest?", "What is the minimum SIP amount?"],
  kyc_security: ["How do I report fraud and what is my liability?", "When does an account become dormant?", "Which documents are accepted for KYC?"],
};

/** 2–3 follow-up suggestions from templates keyed by the primary source document (no LLM call). */
export function followUpsFor(sources: Source[], asked: string): string[] {
  const doc = sources[0]?.doc_id;
  if (!doc || !FOLLOW_UPS[doc]) return [];
  const normalised = asked.trim().toLowerCase();
  return FOLLOW_UPS[doc].filter((q) => q.toLowerCase() !== normalised).slice(0, 3);
}

export function newId(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export const DISCLAIMER = "FinBase assistant answers from official documents effective Oct 1, 2026. Not financial advice.";
