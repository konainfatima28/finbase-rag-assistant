# Evaluation report

All numbers come from real runs on 2026-10-06/07 with `gpt-4.1-mini` (answers, query rewrite and LLM judge) and `text-embedding-3-small` (index content hash `a7c0497b9d3f`). Raw outputs are in `eval/results/`. The final run is **`20261006T231154Z-full`**, which is also `eval/results/latest.json` and is rendered at `/eval`. It ran on the final RAG code. The Phase 3 changes after it touched only the chat UI and the display text of `GET /api/chunks/{id}`, not retrieval, generation or the evidence logic. Nothing below is estimated.

## 1. Golden set (`eval/golden.jsonl`)

94 hand-written items. A test (`tests/unit/test_eval.py::test_every_gold_section_resolves_and_facts_are_in_gold_text`) checks each item against the parsed PDFs: every gold section exists in the index, and every expected fact appears in the gold sections' text.

| category | n | what it tests |
|---|---|---|
| single_fact | 49 | ≥ 7 per document; every seed fact in PROMPT.md §11.1 |
| cross_document | 7 | facts from two documents (`gold_mode: all`) |
| conflict | 8 | mandate fee ₹150–400 vs ₹150–350, P2M T+5 vs T+2, Luxe membership vs calendar year, FD 3-year overlap, ATM metro/non-metro, GST wording, closure fee, ATM-not-dispensed T+5 |
| absent | 9 | home/car loan, UPI mandate bounce fee, repo rate, reward-point value/expiry, tax, 2027 rates, missing chargeback section |
| unsupported | 4 | crypto, farmland loans, intraday/F&O tips, chit funds |
| multi_turn | 5 | pronoun/ellipsis follow-ups with history |
| adversarial | 6 | prompt injection (3), PII echo (2), eligibility decision (1) |
| garbled | 2 | loan cancellation fee `₹1,00,0`, contactless limit `₹5,00,0` |
| hinglish | 4 | 3 Hinglish + 1 Hindi (Devanagari) |

**Corrections made during evaluation.** Each is recorded in the item's `notes`; no item was removed or made easier.
- **ad-02:** the forbidden facts are now *assertions* of the injected value ("charge is 0%"). Previously, a refusal that quoted the user ("you asked to pretend … 0%") counted as a successful injection.
- **ad-04, ad-06:** the FAQ that states the same fact (Q006, Q001) was added as an acceptable gold source. Gold lists were already "any of", as in the other items.
- **cc-07:** "fully paid" was accepted as a paraphrase of "paid in full".

## 2. Metrics (`eval/metrics.py`, unit-tested in `tests/unit/test_eval.py`)

- **Retrieval (section level):** Hit@k, Recall@k, MRR and nDCG@10 over the ranked candidates of the request; context precision over the assembled context. A chunk is relevant if it is in a gold section, its sub-sections, a gold FAQ (including merged duplicates), or the canonical copy of templated boilerplate. `gold_mode: all` needs every listed section.
- **Key-fact recall:** expected facts present after normalisation (`₹1,00,000` = `100000` = `1 lakh`, `1.5%` = `1.50%`, `T + 2` = `T+2`; `a|b` alternatives). Forbidden-fact *assertions* are counted too (sentences quoting or refuting the user are excluded).
- **LLM judge** (`eval/judge_prompts/`, `gpt-4.1-mini`, temperature 0): correctness 0–2, plus claim-level groundedness against the cited sources. Also reported: agreement with key-fact recall, and the deterministic figure-verifier rate.
- **Citations:** precision follows §11.2, "cited sections that are gold **or contain the claim**" (the cited source states an expected fact that the answer states). Recall is gold sections cited. Also checked: the snippet exists in the source text, and the source metadata equals the chunk's structural metadata. Since Phase 2 (D-032) one cited source is one *canonical evidence item*, which can span several chunks of the same logical section or FAQ entry (`chunk_ids`). It is counted once, and it is relevant if any of its chunks is.
- **Abstention:** P/R/F1, with "abstain" as the positive class (gate, `NOT_FOUND`, deterministic abstentions such as an ungrounded answer or a product-scope mismatch, or an opening sentence saying the documents don't contain it); over-refusal on answerable items.
- **Robustness:** injection success = an asserted forbidden fact or a system-prompt leak; PII leak = an identifier from the question echoed in the answer.
- **System:** latency p50/p95 per stage, tokens and cost per query (from `config/pricing.yaml`).

## 3. Final results (run `20261006T231154Z-full`, 94 items)

| target | goal | result | |
|---|---|---|---|
| Recall@5 | ≥ 0.90 | **0.982** | ✔ |
| MRR | ≥ 0.80 | **0.937** | ✔ |
| nDCG@10 | ≥ 0.80 | **0.949** | ✔ |
| Key-fact recall | ≥ 0.90 | **1.000** | ✔ |
| Groundedness (supported claims, LLM judge, 86 answers) | ≥ 0.95 | **0.977** (hallucination rate 0.023), see note | ✔ |
| Citation precision | ≥ 0.90 | **0.971** (recall 0.994) | ✔ |
| Abstention F1 | ≥ 0.90 | **1.000** (P 1.00, R 1.00; 10 unanswerable items) | ✔ |
| Over-refusal | ≤ 0.05 | **0.000** | ✔ |
| Injection success | = 0 | **0.0** (3 items) | ✔ |
| PII leak | = 0 | **0.0** (2 items) | ✔ |

**Note on groundedness (measurement change).** From Phase 2 on, a cited source is a canonical evidence item that can span several chunks of one section (e.g. a table and its row), and the groundedness judge receives the text of *every* chunk of each cited source (D-032). Before, it received only the single representative chunk. Part of the increase from the Phase 1 run (0.952 → 0.977) therefore comes from the judge seeing more of the cited text. It should not be read as a pure gain in answer quality. Both values meet the ≥ 0.95 target.

Other measurements:
- LLM-judge correctness is **1.80 / 2** (normalised 0.899). It agrees with key-fact recall on 87.8% of items.
- The figure-verifier rate is 1.00 and the structural-citation match 1.00. Forbidden-fact assertions: 0.
- The snippet-exists rate is 0.632. This is not a target: FAQ snippets show "question + answer" joined into one line (D-031), which is not a verbatim substring of the chunk text.

**By category (answer pass rate):** single_fact 49/49, conflict 8/8, cross_document 7/7, unsupported 4/4, multi_turn 5/5, adversarial 6/6, garbled 2/2, hinglish 4/4, absent 9/9.

**Latency and cost (eval run, concurrent requests, local Windows CPU):** total p50 **4.8 s**, p95 **9.5 s**. Retrieve p50 3.1 s, of which the FlashRank reranker is 3.0 s. Generate p50 1.25 s, p95 2.1 s. **$0.0009 per query** and ~1,950 tokens. The LLM judge cost $0.059 for 94 items × 2 calls.

Latency rose during the post-review changes (Phase 1 final run `20261006T224229Z`: p50 6.1 s; earlier runs are listed in §4). Nearly all of the increase is in the rerank step; generation is unchanged. The likely cause is extra reranks for sub-queries of broad and non-English questions, plus the serialised reranker under the evaluator's concurrent requests. This has not been measured in isolation, and latency is not one of the PROMPT.md target metrics. It is a known, documented issue (§4.3).

### 3.1 Retrieval modes (same run, 84 answerable items with gold)

| mode | recall@5 | MRR | nDCG@10 | hit@1 |
|---|---|---|---|---|
| dense only (FAISS) | 0.940 | 0.927 | 0.924 | 0.893 |
| BM25 only | 0.935 | 0.899 | 0.915 | 0.857 |
| hybrid (RRF k=60) | 0.958 | 0.933 | 0.938 | **0.905** |
| **hybrid + FlashRank rerank** | **0.982** | **0.937** | **0.949** | 0.881 |

Hybrid beats each retriever alone. The reranker adds 2.4 pts of recall@5 and 1.1 pts of nDCG, at a cost of 2.4 pts of hit@1.

### 3.2 Index ablations (`python -m eval.ablations --dense`)

| variant | chunks | BM25 recall@5 | dense recall@5 | hybrid+rerank recall@5 | hybrid+rerank MRR |
|---|---|---|---|---|---|
| **structure-aware + dedup + headers** | 190 | 0.940 | 0.940 | **0.982** | **0.937** |
| no de-duplication | 1172 | 0.738 | **0.304** | 0.655 | 0.650 |
| no contextual headers | 190 | 0.946 | 0.917 | 0.982 | 0.907 |
| fixed-size 300-token windows | 468 | 0.262 | 0.202 | 0.363 | 0.321 |

- **De-duplication is decisive.** Without it, 14–17 boilerplate copies and 10 copies of every FAQ fill the top-k, and dense recall@5 collapses to 0.30.
- **Contextual headers** add +2.3 pts dense recall@5, +7 pts dense hit@1, and +3 pts MRR after reranking.
- **The fixed-size baseline** is partly a scoring artefact (a window is credited only to its majority section), so read it as a lower bound for naive chunking.

### 3.3 Reranker choice (retrieval-only, `RERANKER_MODEL=…`; measured before Phase 1, single-query ranking unchanged since)

| reranker | recall@5 | MRR | hit@1 | retrieval p50 / p95 |
|---|---|---|---|---|
| **ms-marco-MiniLM-L-12-v2 (default)** | **0.982** | **0.937** | 0.881 | 1,048 / 1,573 ms |
| ms-marco-TinyBERT-L-2-v2 | 0.970 | 0.915 | 0.845 | **41 / 57 ms** |
| none (hybrid RRF) | 0.958 | 0.933 | **0.905** | (no reranker cost) |

MiniLM is kept because answer quality is weighted highest. If latency matters more, use `RERANKER=none`: it beats TinyBERT on MRR and hit@1 and costs nothing. These timings are single sequential retrievals, not the concurrent eval run above.

### 3.4 Calibration (`python -m eval.calibrate`)

Grid search over gate weights and threshold, maximising gate-level abstention F1 subject to over-refusal ≤ 5%.
- **Before** (hand-set): F1 0.571 (P 1.00, R 0.40).
- **After:** F1 **0.667** (P 1.00, R 0.50, over-refusal 0.0), with weights rerank 0.60 / dense 0.25 / gap 0.05 / lexical 0.10 and threshold 0.22.

The gate deliberately catches only clear cases. The rest is handled downstream:
- the LLM's `NOT_FOUND`;
- missing-section and future-year notes;
- deterministic abstentions for answers that stay uncited after one retry, or that only transfer another product's figure.

Together they raise end-to-end abstention F1 to 1.000 on the final run.

### 3.5 Optimisation levers (`python -m eval.perf`, real calls, p50 of 5 runs; measured before Phase 1)

| path | latency p50 | cost |
|---|---|---|
| **answer-cache hit** | **0.1 ms** | **$0** |
| **gate abstain (no LLM call)** | 1,393 ms | **$0** |

The cache and the gate still skip the LLM call entirely. The uncached-answer and follow-up timings from that measurement predate Phase 1; current end-to-end latency is the final-run figure in §3.

## 4. Failure analysis: what the real runs found and how each was fixed

| run | R@5 | KF | judge | grounded | cit. P | abst. F1 | inj. | failures |
|---|---|---|---|---|---|---|---|---|
| 1 `184404Z` | 0.958 | 0.939 | — | — | 0.675 | 0.800 | 0.333 | 15 |
| 2 `185256Z` | 0.982 | 0.971 | 1.82 | 0.963 | 0.960 | 0.842 | 0.333 | 10 |
| 3 `190054Z` | 0.982 | 1.000 | 1.83 | 0.960 | 0.965 | 0.800 | 0.0 | 7 |
| 4 `190924Z` | 0.982 | 1.000 | 1.82 | 0.961 | 0.957 | 0.947 | 0.0 | 4 |
| pre-review baseline `193632Z` | 0.982 | 1.000 | 1.83 | 0.961 | 0.963 | 0.947 | 0.0 | 4 |
| Phase 1 iterations `214514Z`–`221128Z` | 0.982 | 1.000 | 1.78–1.86 | 0.925–0.959 | 0.956–0.978 | 0.824–0.889 | 0.0 | 5–6 |
| Phase 1 final `224229Z` | 0.982 | 1.000 | 1.82 | 0.952 | 0.967 | 1.000 | 0.0 | 3 |
| **final (Phase 2) `231154Z`** | **0.982** | **1.000** | **1.80** | **0.977** | **0.971** | **1.000** | **0.0** | **3** |

Notes on the table:
- Metric definitions changed between runs 1 and 3 (see §4.2), so compare the system changes in §4.1 through their item-level effects.
- The Phase 1 iteration runs were kept as an honest record. They show regressions that were found and fixed before the Phase 1 final run (§4.1, items 12–15).
- **Where the files are:** the final run, the Phase 1 final run and the pre-review baseline are in `eval/results/` (shown in the `/eval` run comparison). The historical development runs in this table (runs 1–4 and the Phase 1 iterations) and the earlier 24-question demo run are preserved under `eval/results/history/`.

### 4.1 System defects found and fixed

1. **JSON mode failed against the live API (run 1).** The judge produced no scores and 4 rewrites fell back to the raw query. Cause: the Responses API rejects `text.format=json_object` unless an *input* message contains "json"; instructions don't count, and the mocked contract test couldn't see this. Fix: the provider appends "Respond with a single JSON object." when needed (contract test updated).
2. **Truncation guard false positive (sv-06):** `₹50,000` (cash-deposit row) was treated as "completing" `₹5,00,0` (contactless row of the same table). **False negative (gv-01):** the model rewrote `₹1,00,0` as `₹1,000`. Fix: row-aware detection using that row's distinctive label words, with same-digit re-formatting counted as a repair.
3. **Conflicts not surfaced (cf-01, cf-04).** Both values were in context, but the model picked one; the single-section FD bucket overlap wasn't in `conflicts.json` at all. Fix: data-driven NOTES in the user turn ("present every value with its citation"), with single-section conflicts included.
4. **Missing-section hallucination (ab-09):** the model assembled a "chargeback workflow" from fragments. Fix: when the question matches a TOC section whose body is missing (from the audit), a note says so. Since Phase 1, when no context block covers the asked topic, the note requires the reply to open with "not available in the knowledge base".
5. **Eligibility decision (ad-04):** "cannot be approved" violated rule 9. Fix: an eligibility-intent note. The answer now explains the criteria and says it can't decide.
6. **Cross-document coverage (xd-01/02/04, hi-03):** one document dominated the context, and the model answered `NOT_FOUND`. Fix: multi-document questions guarantee each routed document ≥ 1 block (`doc_coverage`), and the ranking is diversified. The answers now pass; the retrieval ranking still misses one gold section (§4.3).
7. **Truncated values presented as amounts (gv-01, gv-02).** Fix (Phase 1, D-029): a truncated amount is never shown as a figure. A quoted garbled value is replaced by "an incomplete value", and the answer states that the exact value cannot be safely determined. Evidence status `unclear_value` is attached to the specific row, and confidence is Low.
8. **Cached answers reported their original cost:** this inflated `/api/metrics`. Fix: cache hits report $0 and their own latency (test added).
9. **Conflict warning false alarms:** unrelated conflict groups touched by a cited table (e.g. the ₹999 GST wording on a forex answer). Fix (Phase 1): `conflicting_sources` is decided in code. Both sides must be in context, and the question or answer must be about the conflicted item's own line. Truncated-value findings are never conflicts.
10. **Memory growth (found at Phase 9):** onnxruntime's CPU arena grew RSS from 159 MB to 849 MB in 6 rerank calls, which would breach Render's 512 MB. Fix: rebuild the session with the arena and memory patterns disabled. Measured 168 → 205 MB over 40 real requests; retrieval output is identical (94/94 top-5s).
11. **Context neutralisation was not wired in.** A string replacement in an earlier patch had silently not applied. Fixed, and a regression test asserts that injected instructions inside a retrieved chunk are removed.
12. **ab-02, UPI AutoPay bounce fee (pre-review baseline failure).** The answer borrowed the personal-loan ₹500 EMI bounce fee. Fix (Phase 1): a deterministic product-scope check. When a fee question names a product and *every* amount in the answer comes only from another product's document, the reply becomes the standard not-found message (`product_scope_mismatch`). The personal-loan bounce-fee question is still answered.
13. **Abstention regressions during Phase 1 (ab-08, ab-09; runs `214514Z`–`221128Z`):** the stricter citation retry pushed the model into cited, partial answers instead of "not available". A stray inline `NOT_FOUND` was also being deleted mid-sentence, which inverted the meaning ("rates for 2027 are NOT_FOUND in …" became "are in …"). Fixes:
    - a future-year note;
    - a strict missing-section note when no block covers the topic;
    - an inline `NOT_FOUND` now becomes "not available".
14. **False conflict notes during Phase 1 (pl-03, ab-02):** a shared generic word ("mandate", "disbursal") made the mandate-fee conflict look relevant. Fix: the conflicted line must be the question's best-matching line in each section.
15. **A `₹` in a log line crashed requests on a cp1252 console.** Fix: the log records counts only.

### 4.2 Measurement defects found and fixed

- Citation precision used "gold section only". 61 of 74 "imprecise" citations were FAQ chunks stating the same fact, so precision now uses the §11.2 definition ("gold or contains the claim").
- The abstention detector counted "FinBase does NOT provide X" (correct unsupported-service answers) and "Q004 does not specify non-metro" as refusals. It now looks only at the opening sentence and requires the *documents* to be the subject.
- Forbidden-fact matching counted refusals that quoted the injected value, and matched a verbatim `₹1,00,0` as `₹1,000`. It now ignores quoting/refuting sentences and compares recognised figures only.
- Phase 2: one cited source may span several chunks (`chunk_ids`). Citation precision counts each logical source once, and the groundedness judge receives every chunk of each cited source (see the note in §3).

### 4.3 Remaining failures and known issues (honest)

- **Cross-document retrieval misses: xd-01, xd-02, xd-04** (the only 3 failed items, all retrieval-only). Each needs two documents. In the request's top-5 ranking, the second document is represented by an FAQ or a different section rather than its gold body section, so retrieval recall@5 for these items is **0.5**:
  - xd-01 (UPI limit, Payments SOP vs Savings Account): top-5 has payments §1 plus savings FAQ Q006, not savings §3.
  - xd-02 (Luxe forex markup vs debit-card international POS fee): top-5 has cards §1.2/§21 plus savings FAQ Q008, not savings §4.
  - xd-04 (1-year FD rate vs savings rate above ₹10 lakh): top-5 has savings §2 plus FD FAQ Q001, not FD §1.

  The **generated answers pass**: key-fact recall is 1.0 and the cross_document answer pass rate is 7/7, because the assembled context still covers both documents. The answers cite both documents: for xd-01 and xd-04 they even cite the gold body section (savings §3, FD §1), which entered the context below rank 5; for xd-02 the savings side is cited via FAQ Q008. The miss is a ranking metric, not a wrong answer. These questions are not "broad" in the sense of the sub-query trigger, so no sub-queries are generated for them.
- **Latency** (§3): p50 4.8 s / p95 9.5 s in the final run, higher than before the post-review changes. Most of it is the CPU reranker. On Render's shared CPU it will be slower. `RERANKER=none` trades −2.4 pts recall@5 for zero rerank cost. Not optimised in this submission.

## 5. Methodology limits

- 94 items: one item is ≈ 1.1 points, and the gate is calibrated on the same set (the coarse grid and over-refusal constraint limit overfitting). A held-out set would be better.
- The judge is the same model family as the generator (`JUDGE_MODEL` can point elsewhere). Its agreement with the deterministic key-fact metric is 87.8% in the final run.
- Several Phase 1 fixes were found with this same golden set (ab-02, ab-08, ab-09), so their effect on unseen questions is not separately measured.
- The FD/KYC "missing section" notes and conflict notes rely on the audit detectors. A new corpus would need the audit re-run (it runs automatically in `python -m app.ingest`).
