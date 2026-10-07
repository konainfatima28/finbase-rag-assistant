import type { Metadata } from "next";

import { KnowledgeBase } from "@/components/KnowledgeBase";

export const metadata: Metadata = { title: "About · FinBase Support Assistant" };

const STEPS = [
  ["PII redaction", "Card numbers, Aadhaar, PAN, OTP/PIN, phones and e-mails are masked before anything else."],
  ["Query rewrite", "Follow-ups and Hindi/Hinglish are rewritten into a standalone English query (one small GPT call)."],
  ["Hybrid retrieval", "FAISS dense search (OpenAI embeddings) + BM25 lexical search, fused with Reciprocal Rank Fusion."],
  ["Rerank", "A FlashRank ONNX cross-encoder re-scores the top candidates."],
  ["Context assembly", "Token-budgeted, max 2 blocks per section; table rows bring their table; conflicting sections are always shown together."],
  ["Abstain gate", "Low retrieval confidence → the standard 'not in the knowledge base' reply, without calling the LLM."],
  ["Grounded generation", "GPT answers only from numbered CONTEXT blocks and cites every fact like [1]."],
  ["Verification", "Citations validated, sources built from document metadata, every figure checked against the cited text."],
];

export default function AboutPage() {
  return (
    <div className="mx-auto max-w-4xl space-y-6 px-3 py-6 sm:px-4">
      <section>
        <h1 className="text-xl font-semibold text-ink">How the assistant works</h1>
        <p className="mt-1 text-sm text-muted">Every answer comes from FinBase&apos;s six official policy documents (effective Oct 1, 2026). If the documents don&apos;t contain the answer, the assistant says so.</p>
        <ol className="mt-4 grid gap-2 sm:grid-cols-2">
          {STEPS.map(([title, body], i) => (
            <li key={title} className="rounded-xl border border-line bg-surface p-3">
              <p className="text-sm font-semibold text-ink">
                <span className="mr-1.5 text-brand">{i + 1}.</span>
                {title}
              </p>
              <p className="mt-1 text-xs text-ink-2">{body}</p>
            </li>
          ))}
        </ol>
      </section>
      <KnowledgeBase />
    </div>
  );
}
