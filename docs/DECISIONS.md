# Decisions log

Fixed decisions from `PROMPT.md` §2 are not repeated here. This file records choices made where the spec is ambiguous or where the environment forced a trade-off. Format: decision, alternatives, reason.

## Phase 0

### D-001 Rupee glyph is handled at the font level, then by the `■` rule
- **Decision:** the PDF loader maps every character set in the **ZapfDingbats** font to `■` (U+25A0). The cleaner then applies the single spec rule (`■` → `₹` only before a digit / `(` / space+digit; any other `■` is dropped).
- **Alternatives:** (a) regex on `I\d` from PyMuPDF output; (b) regex on `n\d` from pdfplumber output.
- **Reason:** measured in discovery, the rupee sign in these PDFs is a ZapfDingbats glyph. PyMuPDF decodes it as `I`, pdfplumber as `n`, and pdftotext-style tools as `■`. Letter-based regexes risk corrupting real text (`I2,000` vs a sentence-initial "I"), whereas the font name is unambiguous. All 88/145/235/49/121/48 glyphs map to `₹`, with 0 residuals (DATA_AUDIT §2).

### D-002 Line geometry and font weight for de-wrapping
- **Decision:** a line is a soft-wrap continuation if the previous line reaches the right margin (PDF geometry, 40 pt tolerance) or ends without terminal punctuation and the next starts lower-case. A font-weight change (bold ↔ regular) is always a block boundary. Non-PDF loaders use punctuation and connector-word heuristics.
- **Reason:** FAQ questions are BoldOblique and often reach the margin, so geometry alone glued every question to its answer (observed during development: 37/29/19 "unique" questions instead of 10). With the weight boundary, all six docs give 100 items and exactly 10 unique questions, and no chunk line starts lower-case.

### D-003 Tables: pdfplumber bounding boxes replace text lines; cross-page continuation merge
- **Decision:** PyMuPDF text lines inside a pdfplumber table bbox are dropped and replaced by one `⟦TABLE:id⟧` placeholder at the table's reading position. A headerless table fragment at the top of a page, directly following a table with the same column count, is merged into it (a repeated header is dropped).
- **Reason:** avoids indexing table cells twice (once as loose lines, once as rows). 10 tables span page breaks (DATA_AUDIT §2), and without the merge their tail rows would lose the header.

### D-004 Section with tables → one self-contained chunk when it fits
- **Decision:** if a leaf section (text + linearised tables) is ≤ `CHUNK_MAX_TOKENS`, it becomes one chunk, typed `table` when tables dominate and `policy` otherwise. Each table row additionally becomes a `table_row` fact chunk with `parent_chunk_id`. Oversized sections split text at line boundaries with ~12% overlap, and each table gets its own chunk(s) (preceded by its intro line if that line ends with ':').
- **Alternatives:** always separate table chunks from paragraph chunks.
- **Reason:** keeps intros such as "minimum deposit of ₹1,000 up to ₹50,00,000 per booking:" together with the rate table they introduce, so the LLM sees the conditions with the numbers. This satisfies the spec's "small tables may merge with the preceding paragraph", and row chunks still give exact numeric lookup.

### D-005 Boilerplate = templated heading + measured repetition
- **Decision:** a section is boilerplate if its body heading ends with its own number ("… Standards 7"). Dedup then confirms the repetition (masked-text hash), collapsing each document's templated sections into **one** canonical chunk, which keeps the first copy's full text with tables. All copies are listed in `source_duplicates`.
- **Reason:** both signals agree on all six documents (loans 7–20, cards/savings/payments 4–20, FD 6–20, KYC 7–20). The canonical copy keeps the meaningful tables (PAY-ERR U16/U30/U69/U88, CC-SEC limits, 2FA ₹2,000 rule).

### D-006 Dedup scope is per document
- **Decision:** the "no identical normalised text" invariant and the merge are scoped per `doc_id`.
- **Reason:** the KYC and FD annexes are identical once IDs are masked (FB-STD-KYC-0001 vs FB-STD-FDW-0001). Merging across documents would drop one document's attribution. Within a document there are zero duplicates after dedup (asserted).

### D-007 Front matter is indexed as "Section 0 — Document Information"
- **Reason:** questions such as "who issues the loan policy?" or "effective date?" are answerable only from the header block. Section 0 does not exist in the PDFs, so citations render it as "Document Information" (see D-010 when implemented).

### D-008 Offline token estimator instead of tiktoken
- **Decision:** `count_tokens` counts word and punctuation pieces × 1.1.
- **Reason:** tiktoken downloads its BPE files at first use, which would need network access at Render boot and in CI. Measured against `cl100k_base` on the corpus: mean ratio 0.98 (0.88–1.06). Budgets are soft limits, so this accuracy is enough.

### D-009 Audit FAQ-citation check is calibrated, and finds more than the spec stated
- **Decision:** a section's support for an FAQ = the share of the FAQ's content words found in that section. A citation is flagged when the cited section's support is < 0.25 and another section has at least 2× that support.
- **Reason:** measured distribution: correct in-text citations score ≥ 0.30 and wrong ones ≤ 0.15. Besides the spec's example (loans Q001 → §4.2), the audit found Q002 and Q003 (→ §4.2, actually §6.2) and Q004 (→ §4.1, actually §6.1). All are handled by structural citations.

### D-010 Environment constraints (discovered in Phase 0)
- **Facts:** this machine has no Ollama install, no Docker, no `make`, and no `OPENAI_API_KEY`. Python 3.11 is available through `py -3.11`; Node is v24, not the specified 20 LTS.
- **Decision:** build everything; gate anything that needs a model on its real availability, and report honestly in `BUILD_LOG.md` what could not be executed. `package.json` will declare `engines.node >=20`, with `.nvmrc` = 20 for deploy targets. `scripts/tasks.py` is the cross-platform task runner, and the `Makefile` wraps it.

## Owner decisions overriding PROMPT.md

### D-011 OpenAI for local development AND production — no Ollama (owner decision, 2026-10-06)
- **Overrides:** PROMPT.md §2 "LLM (local/testing) = Ollama", "Switching `LLM_PROVIDER=ollama|openai`", the `indexes/ollama-<model>/` index, nomic task prefixes, `make ingest-ollama`, the docker-compose `ollama` profile, and the "ollama vs openai" ablation.
- **Decision (made by the project owner, not by the agent):** OpenAI is the only implemented provider, in every environment. It is used for chat/generation, query rewriting/translation, LLM-as-judge evaluation, and embeddings. Retrieval stays local and in-process (FAISS + BM25 + RRF + FlashRank). Ollama is not installed, not downloaded, and not a prerequisite. Render receives `OPENAI_API_KEY` through its environment variables.
- **Re-confirmed by the owner (2026-10-06, second instruction):** no Ollama install, models, prerequisite or runtime code. OpenAI is the active, fully supported provider for generation, streaming, query rewriting, translation, judging and embeddings. FAISS, BM25, RRF, FlashRank, retrieval logic, document processing and the non-LLM eval metrics stay local. An inspection after this instruction (`grep -ri ollama` over code/config/requirements) found **zero** Ollama references outside the docs that record this decision.
- **API surface:** new code uses the current OpenAI **Responses API** for generation/streaming (see D-013) and the Embeddings API. The retired Assistants API is not used.
- **What is kept:** the provider abstraction (`app/providers/base.py` protocols + factory), so another provider could be added later without touching retrieval/generation. `LLM_PROVIDER`/`EMBED_PROVIDER` still exist but only accept `openai`. The per-embedder index directory + manifest check stays (switching `OPENAI_EMBED_MODEL`, e.g. to `text-embedding-3-large`, gets its own `indexes/openai-<model>/` and a fail-fast mismatch check).
- **Consequences:** a valid `OPENAI_API_KEY` is required to build the index, run the app and run LLM evals. Tests never need it (FakeLLM / fake embedder / `respx` contract tests only inside `tests/`). The CI retrieval-only eval runs offline from the committed OpenAI index + cached query embeddings (D-013, when implemented). Items that only existed for Ollama are marked `NOT_APPLICABLE (D-011)` in the checklist.

### D-012 Default OpenAI models (verified 2026-10-06)
- **Chat / rewrite / judge default: `gpt-4.1-mini`; embeddings: `text-embedding-3-small` (1536-d).** All are env-configurable (`OPENAI_CHAT_MODEL`, `OPENAI_REWRITE_MODEL`, `JUDGE_MODEL`, `OPENAI_EMBED_MODEL`).
- **Verification:** OpenAI's official "All models", "Deprecations" and "Pricing" pages (developers.openai.com) list `gpt-4.1-mini`, `gpt-4o-mini`, `text-embedding-3-small` and `-large` as available and not scheduled for shutdown. Prices: gpt-4.1-mini $0.40 in / $1.60 out, text-embedding-3-small $0.02 per 1M tokens (`config/pricing.yaml`).
- **Alternatives:** `gpt-4o-mini` is cheaper, but its instruction-following is weaker for strict citation and NOT_FOUND discipline. `gpt-6-luna` is the newest efficient model ($0.10/$0.50); I could not confirm from the docs that it accepts `temperature`/`seed`, which §7 requires (temperature 0, seed). `text-embedding-3-large` costs 6.5× more for a corpus of only 190 chunks.
- **Reason:** `gpt-4.1-mini` is a non-reasoning model, so `temperature=0` and `seed` are honoured (reproducible evals), and it follows long rule-based system prompts well. The provider also retries once without `temperature`/`seed` if a configured model rejects those parameters, so a swap to a newer model does not break the app. `python -m app.providers.check` verifies with the real key that the configured models exist on the account.

## Phase 2

### D-013 OpenAI Responses API (not Chat Completions / Assistants)
- **Decision:** generation and streaming use `client.responses.create(...)` (async), with `instructions` = system prompt, `input` = history + user turn, `temperature` = 0, `max_output_tokens`, and `store=False` (customer text is not retained by OpenAI for later retrieval). JSON mode for query rewriting uses `text.format = json_object`. Embeddings use `client.embeddings.create`. The retired Assistants API is not used.
- **Trade-off:** the Responses API in the installed SDK (openai 3.24.0, inspected) has **no `seed` parameter**; only legacy Chat Completions has one. PROMPT.md §7 says "seed where supported", so the seed setting is kept but not sent. Determinism relies on `temperature=0` plus answer caching.
- **Robustness:** if a configured model rejects `temperature` (some reasoning models do), the provider retries once without it and remembers that. SDK-level exponential backoff (`max_retries=LLM_MAX_RETRIES`) and explicit timeouts apply to every call.

### D-014 Contract tests use `httpx2.MockTransport`, not respx; tests are network-isolated
- **Finding:** openai ≥ 3 ships a vendored `httpx2` transport. respx patches `httpx` and **did not intercept** these requests: during development, test requests with the dummy key `sk-test` reached api.openai.com and got a 401. No real key exists on this machine, so nothing was exposed.
- **Decision:** contract tests inject `httpx2.MockTransport` through the SDK's public `http_client=` parameter. `respx` was removed from `requirements-dev.txt`. An autouse fixture blocks every non-loopback `socket.getaddrinfo`/`connect` during tests. `connect` alone was insufficient on Windows, because the Proactor loop uses ConnectEx; this was verified with a probe, which now fails with "network access blocked in tests: b'api.openai.com'".

### D-015 Index layout and integrity
- Files: `manifest.json`, `chunks.jsonl`, `index.faiss` (IndexFlatIP), `vectors_owner.json` (FAISS row → chunk index), and `bm25.json` (own BM25, JSON; no pickle, so loading a committed index can't execute code).
- FAQ chunks own **two** vectors (Q+A and question-only, §5.3). Dense search keeps each chunk's best row.
- Manifest validation fails fast on: provider ≠ configured; model ≠ configured; dim ≠ known dim of the configured model; vectors not normalised; `content_hash` ≠ recomputed hash (edited manifest). Load also checks: chunks.jsonl sha256 ≠ manifest (partial copy); FAISS ntotal / owners / BM25 doc count ≠ manifest. At query time, a vector with the wrong dimension or not normalised is rejected.
- `content_hash` excludes `built_at`, so re-ingesting the same corpus with the same embedder yields an identical hash (tested). Embeddings are cached in `.cache/embeddings.sqlite` keyed `(provider, model, kind, sha256(text))`, so a re-run makes zero API calls (tested).

## Phases 3–4

### D-016 Rewrite trigger also covers Latin-script Hinglish
- **Spec:** rewrite only when history exists or the text has Devanagari.
- **Decision:** also when ≥ 2 distinct common Hinglish function words appear ("kitna", "hai", "mera", …), because a Latin-script Hinglish query embeds poorly against English documents. The cost is one short JSON call (~150 output tokens max). On any rewrite failure the raw query is used.

### D-017 Conflict surfacing is data-driven
- The audit's conflict detectors now return structural `members` (e.g. `["4.2","21"]`, `["FAQ:Q002","1.2"]`). Ingestion writes the multi-member groups to `conflicts.json` in the index. When context assembly selects one member, it adds the partner chunk even if retrieval missed it (max 2 partners, within budget). Nothing is hard-coded per question, and a new corpus gets its own conflicts.

### D-018 A guessed completion of a truncated value is replaced, not just flagged
- The spec requires never outputting a guessed number for `₹5,00,0` / `₹1,00,0`. If the verifier sees an answer amount that extends the digits of a truncated source value (e.g. `₹1,00,000` vs `₹1,00,0`), the answer is replaced by "the exact amount … appears incomplete or unclear in the source [n] … contact support". This is a deterministic guard on top of prompt rule 6. Quoting the garbled value verbatim is allowed.

### D-019 Streaming contract
- Tokens are held back while the output could still be the `NOT_FOUND` sentinel, so the sentinel never reaches the UI. The `done` event carries the authoritative post-processed answer (invalid markers removed, guards applied), and the UI replaces the streamed text with it. Abstention, cache hits and provider failures emit the final text as a single `token` followed by `sources`, `verification` and `done` (with an `error` event first on provider failure).
- Citation retry ("retry once with a stricter reminder") applies to non-streamed answers. For streamed answers the tokens are already on screen, so an uncited answer is downgraded (confidence ≤ 0.3) with an `ungrounded_no_citations` warning, as §7.2.2 allows.

### D-020 Question line
- The user turn follows §7.1 (`CONTEXT … QUESTION: <raw question>`). When the rewrite changed the query, one extra line `(Interpreted as: <standalone English query>)` is added, so the model answers in the user's language while knowing the resolved meaning.

## Phase 6

### D-021 Node 22 LTS instead of Node 20
- PROMPT.md says Node 20 LTS. Node 20 reached end-of-life on 2026-04-30, and current tooling (vitest 5) requires Node ≥ 22.12. `web/.nvmrc` = 22, and `engines.node` = `>=22.12.0`. Vercel and the Docker image use Node 22. Local development used Node 24.13 (satisfies the range).

### D-022 Own minimal markdown renderer instead of react-markdown
- Model text supports only paragraphs, bullets, numbered lists, **bold** and `[n]` chips. A ~70-line renderer that only builds React elements makes XSS impossible by construction (no HTML parsing at all) and makes citation chips trivial. Tested with `<img onerror>` and `<script>` payloads.

### D-023 Charts without a chart library
- The dashboard needs single-series bars and tables only. CSS bars plus an `sr-only` table give hover values and an accessible table view with zero dependencies. They use one validated hue and no legend (single series), and value labels are in text ink.

## Phase 7 (evaluation-driven)

### D-024 Data-driven NOTES in the user turn (the system prompt stays verbatim)
- **Problem (measured):** with both conflicting values in context, the model still picked one (cf-01, cf-04). It also borrowed a fee across products (ab-02), made an eligibility decision (ad-04), and assembled answers for TOC sections that have no body (ab-09).
- **Decision:** the §7.1 system prompt is unchanged. The user turn gets a `NOTES:` list between CONTEXT and QUESTION, built only from pipeline facts:
  - conflict groups touching the blocks (from the audit detectors);
  - TOC sections missing from the body whose title matches the question (≥ 50% of specific title words), worded "use other blocks if they cover it", because 4 of the 6 missing sections are covered elsewhere;
  - an eligibility-intent note;
  - a per-document block grouping with an attribution reminder.
- **Effect:** cf-01/cf-04/ab-09/ad-04 pass; injection success 0.333 → 0; abstention F1 0.842 → 0.947 together with D-025.

### D-025 Evaluation-metric corrections (documented, not hidden)
- Citation precision now follows PROMPT §11.2 ("gold **or contains the claim**").
- Abstention is detected from the opening sentence, with the documents as subject; "FinBase does not provide X" is an answer, not a refusal.
- Forbidden-fact checks ignore sentences that quote or refute the user, and compare recognised figures only.
- Four golden items were corrected (ad-02, ad-04, ad-06, cc-07); see `docs/EVAL_REPORT.md` §1. No item was removed.

### D-026 Reranker default stays MiniLM-L-12
- Measured: MiniLM recall@5 0.982 / MRR 0.937 at ~1.05 s; TinyBERT 0.970 / 0.915 at 41 ms; no reranker 0.958 / 0.933.
- Quality is weighted highest, so MiniLM stays the default. `RERANKER=none` is the documented latency switch; TinyBERT is not recommended (worse than none on MRR and hit@1).

### D-027 onnxruntime arena disabled for the reranker
- Measured RSS of 159 → 849 MB after 6 rerank calls with defaults (variable sequence lengths keep growing the CPU arena), which would breach Render's 512 MB. The session is rebuilt with `enable_cpu_mem_arena=False`, `enable_mem_pattern=False` and 2 intra-op threads.
- Re-measured: 154–167 MB over 40 reranks, and 205 MB on the live server after 40 chat requests. Retrieval output is identical (94/94 top-5 lists).

### D-028 Deterministic answer guards added after the real runs
- Truncation repair detection is row-aware (D-018 refined).
- An unqualified verbatim garbled value gets an "appears incomplete" note.
- A cross-document figure gets an attribution note and confidence ≤ Medium.
- The "documents differ" badge only appears when the answer is about the conflicted item.
- Cached answers report $0 and their own latency.
- JSON-mode requests add the word "json" to the input, as required by the live Responses API.

## Phase 1 of the post-review plan (2026-10-07): RAG, retrieval and evidence quality

### D-029 Safety decisions move from the LLM's wording into code
- **Evidence status** (`app/generation/evidence.py`) has two distinct values:
  - `unclear_value`: a row or line the question (or answer) is about holds a truncated amount (e.g. `Contactless Tap | Daily Limit: ₹5,00,0`). It is located per row, with column and row label, and the garbled figure is never put in metadata. Effect: confidence Low, plus an explicit "cannot be safely determined" note if the model did not say so.
  - `conflicting_sources`: a genuine value conflict between sections (e.g. loans §21 `₹150 - ₹350` vs §4.2 `₹150 to ₹400`). It is decided from the question plus the evidence, not from the model's phrasing. Effect: both values with citations are appended if the model gave one; confidence is at most Medium. The answer is reworded so that two sections of one manual are never called "different documents" (`same_document: true` in the response).
- Conflict categories now have kinds. Only `value_conflict` raises `conflicting_sources`. `row_inconsistent_or_truncated` is an unclear value (it no longer shows "documents differ" on the contactless answer). Wording variance, scenario dependence and overlapping buckets keep tailored NOTES but no conflict status.
- **Per-row source quality:** a cited table is `suspect_value` only when a row the request is about is incomplete (`unclear_rows`). A processing-fee answer from §21 is `ok` even though the cancellation row of the same table is truncated.
- **Grounding:** an answer still without valid citations after the one stricter retry (or a streamed one) becomes the standard not-found response (`ungrounded_no_citations`). Pipeline words (CONTEXT, blocks, NOTES, NOT_FOUND, system prompt) are scrubbed from replies.

### D-030 Rewrite trigger by vocabulary, sub-queries for broad questions
- **Problem (measured, live):** the Romanised-Hinglish question was translated correctly (≥ 2 Hinglish function words), but answered NOT_FOUND. The translated query retrieved no KYC evidence, and the English question "requirements for opening a savings account" got only the one-line onboarding bullet.
- **Decision:**
  - The rewrite call is made unless the message is confidently plain English: every word must be a common English word or a corpus word. The script used is irrelevant. Clear English first turns are never rewritten (a no-call path; also enforced when the call is made only for sub-queries).
  - Broad questions (requirements / documents / eligibility / process …) get up to 3 sub-queries from the same JSON call. Sub-queries are accepted only when the code's broadness check passes, and none are generated for injection-flagged messages.
  - `Retriever.retrieve_many` runs each sub-query (reranks 8 instead of 12), interleaves the ranked lists (main query first), de-duplicates, and assembles with `multi_query_final_k=8` / 3,800 tokens. The abstain decision stays with the main query; the retrieval score is the best-supported query's.
- **Cost:** one extra cheap rewrite call for broad or non-English questions, plus ~1 s of reranking per sub-query.

### D-031 Context de-duplication, FAQ resolution, related topics, no relative percentages
- A retrieved `table_row` is replaced by its parent table: rows carry their column labels inline, so row + table was duplicate evidence. The row is kept only if the table does not fit the budget. Any chunk whose every normalised line is already in the context is skipped.
- An FAQ chunk holding only its question resolves to the canonical full entry (or is dropped). FAQ display text and snippets drop internal `(Operational case N)`-style labels and show question + answer.
- Related topics after an abstention are shown only when a chunk contains every content term of the interpreted question (none for injection attempts). A high retrieval score alone is not relevance; e.g. "home loan" never matches personal-loan chunks.
- `relevance` in sources is now always `null`. Raw `scores.rerank` / `scores.rrf` are exposed instead: they are not calibrated percentages. Presentation is deferred to Phase 3. The only frontend change is a null guard, so no "NaN%" appears.
- Answer system prompt rules 5/6 were reworded so the phrases the model is asked to use never form an 8-word run of the prompt. With the first wording, the leak guard blocked a correct contactless answer (live run); a regression test covers this.

## Phase 2 of the post-review plan (2026-10-07): evidence & citation architecture

### D-032 Canonical evidence layer after generation; the model's input is unchanged
- **Decision:** a deterministic layer (`app/generation/canonical.py`) runs after all Phase-1 safety post-processing. It groups the cited context blocks into logical sources (`<doc>:section:<id>` / `<doc>:faq:<Qnnn>`), numbers them 1..k in first-citation order, rewrites the answer's markers, and attaches status (`normal` / `unclear_value` / `conflicting_sources`), product scope and a claim → evidence map.
- **Why after generation, not in the prompt:** the frozen Phase-1 baseline was measured with the current context format. Merging blocks before the LLM would change what the model sees. Post-processing gives the same canonical contract with no change in generation behaviour.
- **Section-level identity:** all chunks of one section (table + rows, split chunks) are one evidence item, because a citation names a section, not a chunk. FAQ entries are keyed by canonical FAQ id. Different sections are never merged, which keeps conflicts visible.
- **Public contract:** `relevance` and raw `scores` were removed from evidence items (not a meaningful percentage). Debug scores stay in `meta`. `sources` keeps its old fields (backward compatible) plus the canonical ones; `evidence` gains `items` and `claims`.
- **Eval harness:** a source may now span several chunks (`chunk_ids`). Citation precision counts each logical source once, and the groundedness judge receives every chunk of each cited source.
- **Known limitation:** streamed `token` events show the model's raw block numbers until `done` replaces the text with the renumbered answer.

## Phase 3 of the post-review plan (2026-10-07): chat UI polish

### D-033 Simple chat over the canonical evidence contract
- UI/UX is 5% of the assignment, so the UI stays deliberately simple: no new libraries, no component system, no animations beyond the existing ones.
- Evidence is shown on demand. Compact chips with canonical labels replace always-expanded source cards; an inline citation or a chip opens one evidence drawer.
- Status is communicated once, from the API's deterministic status (one notice per conflict or incomplete value), not as extra badges. No scores or percentages are displayed.
- Citation markers are hidden while streaming, because only the `done` event carries final citation numbers (D-032).

