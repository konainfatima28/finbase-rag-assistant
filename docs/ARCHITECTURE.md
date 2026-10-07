# Architecture

FinBase Support Assistant is a retrieval-augmented customer-support assistant. It answers **only** from six FinBase policy PDFs, cites the exact document, section and page, and abstains when the documents do not contain the answer.

## Components

```mermaid
flowchart LR
  subgraph Offline["Offline (CLI)"]
    PDF[6 PDFs<br/>data/raw] --> L[Loaders<br/>PyMuPDF + pdfplumber]
    L --> C[Cleaner<br/>glyph · de-glue · unwrap · markdown]
    C --> S[Section tree<br/>TOC · headings · FAQ items]
    S --> K[Structure-aware chunker<br/>contextual headers · tables · rows · FAQs]
    K --> D[De-duplicator<br/>190 of 1172 chunks]
    D --> E[OpenAI embeddings<br/>text-embedding-3-small]
    E --> I[(indexes/openai-text-embedding-3-small<br/>FAISS · BM25 · chunks · manifest · conflicts)]
    S --> A[Audit + conflict detectors] --> I
  end
  subgraph Runtime["Runtime (FastAPI on Render)"]
    Q[User message] --> P[PII redaction]
    P --> R[Rewrite / translate<br/>gpt-4.1-mini, only if needed]
    R --> H[Hybrid retrieval<br/>FAISS + BM25 → RRF k=60<br/>router boost · boilerplate weight]
    H --> RR[FlashRank rerank]
    RR --> CA[Context assembly<br/>budget · per-section cap · de-dup (row→table, FAQ resolve) · conflict partners]
    CA --> G{Abstain gate}
    G -- low confidence --> NF[Not-found message<br/>+ related sources]
    G -- ok --> LLM[gpt-4.1-mini<br/>Responses API, streamed]
    LLM --> V[Post-processing<br/>NOT_FOUND · citations · figure verifier · leak guard · confidence]
    V --> SSE[SSE / JSON]
  end
  I --> H
  SSE --> UI[Next.js on Vercel<br/>chat · sources · /eval]
```

## Request lifecycle (`POST /api/chat`)

| step | module | notes |
|---|---|---|
| 1. Validation | `app/api/schemas.py` | 1–2,000 chars, blank → 422, history ≤ 50 items, body ≤ 64 KB (413) |
| 2. PII redaction | `app/safety/pii.py` | card numbers (Luhn-valid or not), Aadhaar, PAN, OTP/PIN/CVV/password phrases, phone numbers and e-mail addresses are masked before logging, retrieval or the LLM |
| 3. Injection flags | `app/safety/injection.py` | Flagged queries are logged. Figures quoted in a flagged question are not trusted by the verifier. |
| 4. Rewrite | `app/generation/rewrite.py` | Only for follow-ups or Hindi/Hinglish. One JSON call; falls back to the raw query on any failure. |
| 5. Retrieval | `app/retrieval/pipeline.py` | Dense top-20 + BM25 top-20 → RRF (k = 60). Weights: boilerplate × 0.6, FAQ × 0.95, router × 1.15 (soft boost, never a filter). FlashRank reranks the top 12. |
| 6. Context | `app/retrieval/context.py` | ≤ 5 retrieved blocks, ≤ 2 per section, ≤ 2,500 tokens (broad questions with sub-queries: ≤ 8 blocks, ≤ 3,800 tokens; D-030). A retrieved table row is replaced by its parent table, and content already in the context is skipped (no duplicate evidence). An orphan FAQ question resolves to the full FAQ entry. A conflict member pulls in its partner. A multi-document question gets ≥ 1 block per routed document (`doc_coverage`), and its ranking is diversified. |
| 7. Gate | `app/retrieval/gate.py` | Confidence = weighted reranker score, dense cosine, score gap and lexical overlap. Below the calibrated threshold → abstain without calling the LLM. |
| 8. Generation | `app/generation/answer.py`, `prompts.py` | Verbatim §7.1 system prompt; temperature 0; ≤ 500 output tokens; `store=false`. The user turn adds data-driven NOTES (detected conflicts, TOC sections missing from the body, eligibility requests, per-document block grouping). |
| 9. Post-processing | `citations.py`, `verifier.py`, `confidence.py` | Drops invalid `[n]` markers; sources come from cited chunk metadata only. Every figure is checked against the cited text. A guessed completion of a truncated value is replaced. The prompt-leak guard runs. Output is labelled High/Medium/Low. |
| 9b. Evidence | `app/generation/canonical.py` | After generation: cited blocks → canonical evidence items (one per section / FAQ entry), citation numbers in first-citation order (markers rewritten), status `normal` / `unclear_value` / `conflicting_sources`, product scope, claim → evidence map. No scores or percentages in the public contract (D-032). |
| 10. Response | `app/api/sse.py` | SSE events `meta → token* → sources → verification → done` (or `error`), with a heartbeat every 15 s. Client disconnect cancels the OpenAI stream. |

## Why these choices

- **Structure-aware chunks (one per leaf section).** The policy content is short sections of bullets and tables, so chunking by section keeps each rule with its conditions. Contextual headers put the document title, code, section and page into every embedded text. This lets "Section 3" in six different documents be told apart.
- **De-duplication before indexing.** Sections 4/6/7–20 of each PDF and the 100-item FAQs are templated repeats. Without dedup, the top-k is flooded with copies of one passage: measured recall@5 without → with dedup is 0.738 → 0.940 for BM25, 0.304 → 0.940 for dense and 0.655 → 0.982 for hybrid+rerank (`eval/results/ablations.json`).
- **FAISS `IndexFlatIP`.** Only 250 vectors (190 chunks + 60 FAQ-question vectors), so an exact flat index is optimal: no recall loss, deterministic, a few MB of RAM, zero ops, and it ships inside the repo. Chroma, Qdrant, pgvector and Pinecone add a server or network hop, persistence and cost for no accuracy gain at this scale.
- **Hybrid search + RRF.** BM25 nails exact codes and figures ("U69", "₹5,00,000", "FOIR"); dense retrieval handles paraphrases and Hindi/Hinglish after rewrite. RRF fuses ranks without score calibration.
- **FlashRank (ONNX).** A cross-encoder reranker without torch. The model is baked in at build time; if it fails to load, the fused order is used. The ONNX session runs with the CPU memory arena disabled: measured RSS of 205 MB after 40 requests, vs 849 MB after 6 reranks with defaults.
- **Plain Python orchestration (no LangChain/LlamaIndex).** Every stage is a small typed function with its own tests. This gives full control over citation and verification logic and fewer dependencies on the latency-critical path.
- **OpenAI for everything model-related (owner decision D-011).** `gpt-4.1-mini` (non-reasoning, honours temperature 0) and `text-embedding-3-small`. The provider interface (`app/providers/base.py`) keeps a second provider possible.

## Hallucination-mitigation layers

1. **Retrieval gating.** Low-confidence retrieval never reaches the LLM.
2. **Grounded prompt.** It allows only facts from numbered CONTEXT blocks, requires citing every sentence, surfacing conflicts, and never completing truncated values.
3. **`NOT_FOUND` sentinel.** The model's way to abstain. It never reaches the UI and is replaced by the standard message with KB contacts.
4. **Citation validator.** Markers that don't map to a block are dropped. An uncited answer gets one stricter retry, then a warning and Low confidence.
5. **Structural sources.** Built from chunk metadata, never from section numbers written in the text. This handles the FAQ Q001–Q004 trap.
6. **Figure verifier.** Every amount, %, duration, T+n and count must appear in the cited chunks after normalisation. Otherwise a visible "unverified figures" badge is shown, and the answer is never labelled High.
7. **Truncated-value guard.** `₹1,00,0` / `₹5,00,0` can never be "completed" (row-aware: same-digit re-formatting counts). A verbatim garbled value gets an "appears incomplete" note.
7b. **Cross-document attribution guard.** A figure that only one of a sentence's cited documents states gets an attribution note, and confidence is capped at Medium.
8. **Conflict surfacing.** Contradictory sections are always retrieved together, the model is told which blocks differ, and the UI flags it, but only when the answer is about the conflicted item.
9. **Injection defences.** Prompt rule 10, instruction-like lines stripped from the context, untrusted figures from flagged questions, and an output-side prompt-leak guard.

## Data model and index

Index directory: `indexes/<provider>-<model>/`. It contains `manifest.json` (embedder identity, dim, normalisation, counts, source PDF sha256s, chunker version, content hash), `chunks.jsonl`, `index.faiss`, `vectors_owner.json`, `bm25.json` and `conflicts.json`. At startup the API verifies that the manifest matches the configured embedder (provider, model, dim, normalisation, content hash, file consistency) and refuses to start otherwise. Every query vector is also checked against the index dimension.
