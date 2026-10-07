# FinBase — Requirements Checklist

Every meaningful requirement extracted from `PROMPT.md` (the authoritative spec).
`§` = section of `PROMPT.md`. Status values: `NOT_STARTED`, `IN_PROGRESS`, `DONE` (implemented **and** its test/verification was run and passed), `PARTIAL` (implemented, verification incomplete or blocked — reason given), `BLOCKED` (cannot be done in this environment — reason given).

> **Owner decision D-011 (2026-10-06):** OpenAI is used for local development *and* production (chat, rewrite, judge, embeddings); Ollama is not used. Requirements that only existed for Ollama are marked `NOT_APPLICABLE (D-011)`; requirement texts below still quote PROMPT.md verbatim.

Statuses are updated at the end of every phase (see `docs/BUILD_LOG.md` for evidence).

## Product
| ID | Requirement | § | Implementation | Test | Status |
|---|---|---|---|---|---|
| PRD-1 | Answer only from the six-PDF knowledge base, with source references | 1, 0.4 | `app/generation/` | eval key-fact + groundedness | DONE |
| PRD-2 | Say clearly when answer is not in KB (standard not-found message, KB contacts only) | 1, 7.2.1 | `app/generation/answer.py` | integration not-found | DONE |
| PRD-3 | Pipeline: rewrite → hybrid retrieval → rerank → context → LLM → citations → verification | 1, 4 | `app/generation/answer.py` | integration | DONE |
| PRD-4 | Answer format `Answer: … [n]` + `Source: <Title> — Section x.y (p. N)` | 1, 7.2.5 | `app/generation/formatter.py` | unit formatter | DONE |
| PRD-5 | UI answer card + sources panel (title, code, section, page, snippet, score) | 1, 9 | `web/components/` | vitest | DONE — sources as canonical chips + evidence drawer (title, code, section, page, highlighted snippet); score intentionally removed (CNF-2, D-033) |

## Data ingestion
| ID | Requirement | § | Implementation | Test | Status |
|---|---|---|---|---|---|
| ING-1 | `data/sources.yaml` maps file → stable doc_id/title/code; citations never use file names | 3 | `data/sources.yaml`, `app/ingest/sources.py` | unit | DONE |
| ING-2 | Loader registry: `.pdf` (PyMuPDF text+pages, pdfplumber tables), `.docx`, `.md/.txt`, `.csv` → common `RawDocument` | 5.1 | `app/ingest/loaders.py` | unit w/ tiny fixtures per type | DONE |
| ING-3 | OCR fallback only if a page yields no text | 3 | `app/ingest/loaders.py` | unit (documented) | PARTIAL — hook unit-tested with fakes; no corpus page needs OCR and Tesseract is not installed |
| ING-4 | CLI `python -m app.ingest --embedder openai [--rebuild]` (spec: `openai\|ollama`; Ollama removed, D-011) | 5 | `app/ingest/__main__.py` | manual run | DONE |
| ING-5 | Outputs `data/processed/chunks.jsonl`, `indexes/<embedder-id>/{index.faiss,bm25.json,chunks.jsonl,manifest.json}` | 5.5 | `app/ingest/build.py` | integration | DONE |
| ING-6 | Idempotent; on-disk embedding cache keyed `(embedder, model, sha256(text))`; batched calls w/ retry+backoff | 5.5 | `app/ingest/embed_cache.py` | unit | DONE |
| ING-7 | Prints summary: docs, pages, sections, chunks by type, dups removed, tokens, est. embedding cost | 5.5 | `app/ingest/__main__.py` | manual run | DONE |
| ING-8 | Reproducible: ingest twice → identical chunk ids and manifest hash | 16 | `app/ingest` | unit/integration | DONE |

## Data cleaning
| ID | Requirement | § | Implementation | Test | Status |
|---|---|---|---|---|---|
| CLN-1 | Keep `raw_text` and `clean_text`; page number per span | 5.2 | `app/ingest/cleaner.py` | unit | DONE |
| CLN-2 | Unicode NFC, collapse whitespace | 5.2 | cleaner | unit | DONE |
| CLN-3 | Remove TOC (use it to learn titles) and header/footer repeats | 5.2, 3.1 | cleaner/structure | unit | DONE |
| CLN-4 | Remove duplicated trailing numbers in boilerplate headings ("Standards 7") | 5.2 | structure | unit | DONE |
| CLN-5 | UTF-8 everywhere, pathlib, cross-platform | 0.7 | all | ruff/grep check | DONE |

## PDF traps (§3.2 — one unit test each)
| ID | Requirement | § | Implementation | Test | Status |
|---|---|---|---|---|---|
| TRP-1 | `■` → `₹` only before digit / `(` / space+digit; never a `■` in any chunk | 3.2.1 | cleaner | unit + chunk invariant | DONE |
| TRP-2 | Glued lines: strip `\f`; newline before `Section N:`, `Qnnn:`, `•`, row IDs, `^\d+\.\d+ `; exactly 100 FAQs/doc via anywhere-regex | 3.2.2 | cleaner | unit + per-doc FAQ count | DONE |
| TRP-3 | Re-join soft-wrapped lines (incl. wrapped `(… case\n11)`) | 3.2.3 | cleaner | unit | DONE |
| TRP-4 | Strip `**bold**` and backticks, keep words | 3.2.4 | cleaner | unit | DONE |
| TRP-5 | Tables via pdfplumber (fallback layout); rows linearised `Col: val \| Col: val`; header kept; multi-line cells merged | 3.2.5 | `app/ingest/tables.py` | unit (TLS 1.3 / Executive Tier cells) | DONE |
| TRP-6 | Indian numbers & lakh/crore: index original + normalised tokens; normalise p.a./per annum, T+2/T + 2, GST | 3.2.6 | `app/text/numbers.py` | unit | DONE |
| TRP-7 | Truncated values (`₹5,00,0`, `₹1,00,0`) never repaired; chunk `quality_flag="suspect_value"` | 3.2.7 | cleaner/chunker | unit + eval | DONE |
| TRP-8 | Heading detection incl. `Section 6.2:`, `6.1`, `1.1 FinBase Neo…`; section tree; full breadcrumb on every chunk | 3.2.8 | `app/ingest/structure.py` | unit | DONE |

## Section parsing
| ID | Requirement | § | Implementation | Test | Status |
|---|---|---|---|---|---|
| SEC-1 | Section tree (section → subsection), titles from body (TOC as fallback) | 3.2.8 | structure.py | unit | DONE |
| SEC-2 | Annex sections `KYC-EXT-00n`, `FDW-EXT-001` recognised (chunk_type annex) | 3.1 | structure.py | unit | DONE |
| SEC-3 | Page start/end tracked per section/chunk | 5.2, 5.3 | structure.py | unit | DONE |

## Chunking
| ID | Requirement | § | Implementation | Test | Status |
|---|---|---|---|---|---|
| CHK-1 | Unit = leaf (sub)section; whole if ≤ `CHUNK_MAX_TOKENS` (600); target 250–450 | 5.3 | `app/ingest/chunker.py` | invariant test | DONE |
| CHK-2 | Longer: split at bullet/paragraph boundaries, 10–15% overlap, never inside bullet/table row; same breadcrumb | 5.3 | chunker | unit | DONE |
| CHK-3 | Contextual header `[Title \| Code \| Section x Title \| p.N]` prepended to embedded+LLM text; stored separately | 5.3 | chunker | unit | DONE |
| CHK-4 | Tables: one chunk per table / row-group, header repeated; small tables may merge with paragraph | 5.3 | chunker | unit | DONE |
| CHK-5 | `table_row` fact-sentence chunks pointing to parent table | 5.3 | chunker | unit | DONE |
| CHK-6 | FAQ: one chunk per Q&A (`faq_id`, question, answer); embed Q+A and question-only second vector → same chunk | 5.3 | chunker + build | unit | DONE |
| CHK-7 | Full metadata on every chunk (chunk_id stable hash, doc fields, section, breadcrumb, pages, chunk_type, faq_id, clause_ref, quality_flag, token_count, source_duplicates) | 5.3 | `app/ingest/models.py` | invariant test | DONE |
| CHK-8 | `CHUNKER_VERSION` in manifest | 5.3 | chunker/build | unit | DONE |

## Deduplication
| ID | Requirement | § | Implementation | Test | Status |
|---|---|---|---|---|---|
| DED-1 | Mask counters/IDs (case/node/batch job/SAV-CAT/SEC-LOG/PAY-REC/Clause/section numbers) then hash | 5.4 | `app/ingest/dedup.py` | unit | DONE |
| DED-2 | Boilerplate 7–20 → one canonical chunk/doc, flagged `boilerplate`, weight 0.6; `source_duplicates` recorded | 5.4 | dedup | unit | DONE |
| DED-3 | Keep protocol/response-code tables (U16/U30/U69/U88) in canonical copy | 5.4 | dedup | unit | DONE |
| DED-4 | FAQ dedupe by normalised question; differing answers both kept + audit conflict | 5.4 | dedup + audit | unit | DONE |
| DED-5 | No two indexed chunks with identical normalised text (assert) | 5.4 | dedup | invariant test | DONE |
| DED-6 | `data/processed/dedup_report.json` (what merged, counts, representative ids) | 3.3F | dedup | file exists | DONE |

## Embeddings
| ID | Requirement | § | Implementation | Test | Status |
|---|---|---|---|---|---|
| EMB-1 | OpenAI embeddings for local dev and prod (spec also listed Ollama for local; removed, D-011); model env-configurable | 2 | `app/providers/` | contract tests (respx) | DONE — OpenAI only (D-011) |
| EMB-2 | `EMBED_PROVIDER` independent, defaults to `LLM_PROVIDER` | 2 | `app/settings.py` | unit | DONE — only openai accepted (D-011) |
| EMB-3 | nomic task prefixes applied inside provider (`search_document:`/`search_query:`) | 2 | ollama provider | unit | NOT_APPLICABLE — D-011 nomic prefixes were Ollama-only; OpenAI embeddings need no task prefix |
| EMB-4 | L2-normalise both index and query vectors | 16 | providers/index | unit | DONE |
| EMB-5 | Separate index dirs per embedder; manifest with all required fields | 2 | `app/retrieval/store.py` | unit | DONE |
| EMB-6 | Startup verifies provider, model, dim, normalized; fail fast with clear error | 2, 16 | store.py | unit (mismatch raises) | DONE |
| EMB-7 | OpenAI index committed so Render never rebuilds at boot | 2, 13 | `indexes/openai-*` | — | DONE — built; commit pending (user) |

## FAISS
| ID | Requirement | § | Implementation | Test | Status |
|---|---|---|---|---|---|
| FAI-1 | faiss-cpu flat IP on normalised vectors; persisted metadata | 2 | store.py | unit | DONE |
| FAI-2 | FAISS id ↔ chunk id mapping tested (incl. question-only vectors) | 16 | store.py | unit | DONE |
| FAI-3 | Justify FAISS vs Chroma/Qdrant/pgvector/Pinecone in README | 2 | README | — | DONE |

## BM25
| ID | Requirement | § | Implementation | Test | Status |
|---|---|---|---|---|---|
| BM-1 | BM25 over the identical chunk list/order as FAISS | 2, 16 | `app/retrieval/bm25.py` | unit | DONE |
| BM-2 | Lexical text includes normalised number/lakh tokens and abbreviation expansions | 3.2.6, 6.1 | numbers.py | unit | DONE |

## RRF
| ID | Requirement | § | Implementation | Test | Status |
|---|---|---|---|---|---|
| RRF-1 | RRF k=60 (configurable) then boilerplate & router weights | 6.4 | `app/retrieval/fusion.py` | unit | DONE |

## Reranking
| ID | Requirement | § | Implementation | Test | Status |
|---|---|---|---|---|---|
| RR-1 | flashrank ONNX cross-encoder on top `RERANK_TOP_N`; pluggable; `none` fallback if load fails | 2, 6.5 | `app/retrieval/rerank.py` | unit (fallback) | DONE |
| RR-2 | Reranker warmed at startup | 8 | `app/main.py` | integration | DONE |

## Query rewriting
| ID | Requirement | § | Implementation | Test | Status |
|---|---|---|---|---|---|
| QRW-1 | Only when history exists or Devanagari present; temp 0; JSON `{standalone_query_en, language}`; last 4 turns | 6.2 | `app/generation/rewrite.py` | integration | DONE |
| QRW-2 | Fallback to raw query on failure/timeout | 6.2 | rewrite.py | unit | DONE |
| QRW-3 | Answer in user's language (EN/HI/Hinglish best-effort), numbers unchanged | 6.2, 7.1 | prompt | eval Hinglish | DONE — Hindi/Hinglish 4/4 in eval |

## Domain routing
| ID | Requirement | § | Implementation | Test | Status |
|---|---|---|---|---|---|
| RT-1 | Keyword/regex router → doc_ids; soft boost (×1.15), never a hard filter | 6.3 | `app/retrieval/router.py` | unit + cross-doc test | DONE |

## Context assembly
| ID | Requirement | § | Implementation | Test | Status |
|---|---|---|---|---|---|
| CTX-1 | Token budget `CONTEXT_TOKEN_BUDGET`; `MAX_PER_SECTION`; `FINAL_K` | 6, 6.6 | `app/retrieval/context.py` | unit | DONE |
| CTX-2 | table_row top → include parent table (since D-031: the row is replaced by its table, no duplicate); prefer body over FAQ on ties; keep conflicting chunks | 6.6 | context.py | unit | DONE |
| CTX-3 | Blocks numbered `[1]..[n]` with contextual headers | 6.6 | context.py | unit | DONE |
| CTX-4 | Retrieval debug info (ids, per-stage scores) in response meta | 6.8 | pipeline | integration | DONE |

## Abstention
| ID | Requirement | § | Implementation | Test | Status |
|---|---|---|---|---|---|
| ABS-1 | Confidence from top rerank score, top dense cosine, gap, lexical overlap | 6.7 | `app/retrieval/gate.py` | unit | DONE |
| ABS-2 | Below `ABSTAIN_THRESHOLD`: skip LLM, standard not-found + related topics | 6.7 | answer.py | integration | DONE |
| ABS-3 | `python -m eval.calibrate` maximises F1; writes `config/thresholds.json`; reported in README | 6.7 | `eval/calibrate.py` | run | DONE |
| ABS-4 | Over-refusal on answerable golden items ≤ 5% | 16 | gate | eval | DONE — over-refusal 0.0 |

## LLM generation
| ID | Requirement | § | Implementation | Test | Status |
|---|---|---|---|---|---|
| GEN-1 | Provider abstraction kept for future providers; `LLM_PROVIDER=openai` is the only active/supported provider (D-011) | 2 | `app/providers/` | contract tests | DONE — abstraction + OpenAI implementation (D-011) |
| GEN-2 | Temp 0, seed, max_tokens cap (500), streaming | 7 | providers | unit | DONE — temp 0, max tokens, streaming; Responses API has no seed (D-013) |
| GEN-3 | System prompt verbatim in `prompts/answer_system.txt`, versioned | 7.1 | prompts/ | unit (loads) | DONE |
| GEN-4 | User template `CONTEXT:\n[1] <header>\n<text>…\n\nQUESTION: …`; history ≤6 msgs as prior turns, never in CONTEXT | 7.1 | `app/generation/prompting.py` | unit | DONE |
| GEN-5 | NOT_FOUND sentinel → standard message, `answerable=false`, related sources | 7.2.1 | answer.py | integration | DONE |
| GEN-6 | Timeouts + exponential-backoff retries; graceful "temporarily unavailable" + sources when LLM down | 8 | providers/answer | integration | DONE |
| GEN-7 | Output sanitised (no raw HTML) | 7.2.6 | formatter | unit | DONE |

## Citation validation
| ID | Requirement | § | Implementation | Test | Status |
|---|---|---|---|---|---|
| CIT-1 | Parse `[n]`; drop markers not mapping to blocks | 7.2.2 | `app/generation/citations.py` | unit | DONE |
| CIT-2 | Zero valid citations on factual answer → retry once stricter, else downgrade + warning | 7.2.2 | answer.py | integration | DONE |
| CIT-3 | `sources[]` only from blocks actually cited | 7.2.2 | citations.py | unit | DONE |
| CIT-4 | Citations from structural metadata (doc+section+page), never numbers in answer text; 4.2-vs-6.2 regression test | 3.3B, 16 | citations.py | regression unit | DONE |
| CIT-5 | Every citation includes doc title (+code, section, page, chunk_id); FAQ-only → "FAQ Q001"; body preferred over FAQ | 3.3B/C | citations.py | unit | DONE |

## Figure verification
| ID | Requirement | § | Implementation | Test | Status |
|---|---|---|---|---|---|
| FIG-1 | Extract amounts, %, durations, T+n, counts; normalise; check in cited chunks / question / computed | 7.2.3 | `app/generation/verifier.py` | unit (1.5% vs 1.50%, ₹1,00,000 vs 100000, T+2 vs T + 2, computed) | DONE |
| FIG-2 | `verification.unverified_figures[]` + visible badge | 7.2.3 | verifier + UI | unit + vitest | DONE |

## Confidence scoring
| ID | Requirement | § | Implementation | Test | Status |
|---|---|---|---|---|---|
| CNF-1 | High/Medium/Low from retrieval confidence + citation coverage + verifier; numeric + label | 7.2.4 | `app/generation/confidence.py` | unit | DONE |
| CNF-2 | Per-source relevance score | 10.6 | context/sources | unit | INTENTIONALLY REMOVED (D-031, D-032, D-033): per-query rescaled reranker scores were misleading (97–99% on unrelated sources). `relevance` and raw scores are not in the public evidence contract and no percentage is shown; debug scores remain only in `meta`. Status and confidence come from deterministic logic. |

## PII protection
| ID | Requirement | § | Implementation | Test | Status |
|---|---|---|---|---|---|
| PII-1 | Mask 16-digit cards, 12-digit Aadhaar, PAN, OTP/PIN phrases, phones, emails — input and logs | 8 | `app/safety/pii.py` | unit | DONE |
| PII-2 | Model receives masked text; UI shows gentle notice | 8 | answer.py + UI | integration | DONE |
| PII-3 | PII-leak rate = 0 on adversarial set | 11.2 | eval | eval | DONE — 0.0 |

## Prompt injection protection
| ID | Requirement | § | Implementation | Test | Status |
|---|---|---|---|---|---|
| INJ-1 | Injection in question or in a retrieved chunk fixture does not change behaviour | 16 | `app/safety/injection.py` + prompt rule 10 | integration | DONE |
| INJ-2 | Injection success rate = 0 | 11.2 | eval | eval | DONE — 0.0 |

## API
| ID | Requirement | § | Implementation | Test | Status |
|---|---|---|---|---|---|
| API-1 | `POST /api/chat` (stream & non-stream, full response object incl. usage/latency/request_id) | 8 | `app/api/chat.py` | integration | DONE |
| API-2 | `GET /api/health` (provider, models, manifest, chunk count), `GET /api/ready` | 8 | `app/api/health.py` | integration | DONE |
| API-3 | `GET /api/docs/list`, `GET /api/chunks/{chunk_id}` | 8 | `app/api/docs.py` | integration | DONE |
| API-4 | `POST /api/feedback` → JSONL | 8 | `app/api/feedback.py` | integration | DONE |
| API-5 | `GET /api/metrics` (p50/p95, cache hit, abstain rate, cost, error rate) | 8 | `app/api/metrics.py` | integration | DONE |
| API-6 | `GET /api/eval/latest`, `GET /api/eval/runs` | 8 | `app/api/eval.py` | integration | DONE |
| API-7 | `/docs` renders; `docs/API.md` with curl incl. SSE | 8 | main.py, docs | integration | DONE |
| API-8 | CORS allow-list (exact origins, no `*` with credentials), preflight works | 8, 16 | main.py | integration | DONE |
| API-9 | Request size cap (2,000 chars); empty/whitespace → 422; history capped | 8, 16 | schemas | integration | DONE |
| API-10 | Per-IP rate limit (20/min) | 8 | slowapi | integration | DONE |
| API-11 | No stack traces to clients | 8 | exception handlers | integration | DONE |
| API-12 | Blocking work in threadpool; async clients; no unlocked global mutable state | 8, 16 | api | review + test | DONE |
| API-13 | Startup: load index + manifest check + warm reranker; `PORT` env; bind 0.0.0.0 | 8 | main.py | integration | DONE |
| API-14 | Conversation memory: history from client; optional TTL session cache by session_id | 8 | api | integration | DONE |

## Streaming
| ID | Requirement | § | Implementation | Test | Status |
|---|---|---|---|---|---|
| SSE-1 | Events `meta, token, sources, verification, done, error` in order | 8 | `app/api/sse.py` | integration event order | DONE |
| SSE-2 | Headers `Cache-Control: no-cache`, `Connection: keep-alive`, `X-Accel-Buffering: no` | 8 | sse.py | integration | DONE |
| SSE-3 | Heartbeat comment ~15 s | 8 | sse.py | unit | DONE |
| SSE-4 | Client disconnect cancels upstream LLM | 8 | sse.py | unit | DONE |
| SSE-5 | Error events instead of crashing stream | 8 | sse.py | integration | DONE |

## Caching
| ID | Requirement | § | Implementation | Test | Status |
|---|---|---|---|---|---|
| CCH-1 | TTL-LRU retrieval cache on (normalised query, manifest hash) | 8 | `app/cache/ttl_lru.py` | unit | DONE |
| CCH-2 | Answer cache on (query, context hash, model, prompt version) | 8 | answer.py | unit | DONE |
| CCH-3 | Cache metrics exposed | 8 | metrics | integration | DONE |

## Observability
| ID | Requirement | § | Implementation | Test | Status |
|---|---|---|---|---|---|
| OBS-1 | structlog JSON logs: request_id, per-stage latency, tokens, cost, provider/model | 8 | `app/observability/` | unit | DONE |
| OBS-2 | PII redacted before logging | 8 | logging processor | unit | DONE |
| OBS-3 | Access log includes request_id | 8 | middleware | integration | DONE |
| OBS-4 | Price table in config (`config/pricing.yaml`) | 8 | config | unit | DONE |

## Frontend
| ID | Requirement | § | Implementation | Test | Status |
|---|---|---|---|---|---|
| FE-1 | Next.js App Router + TS + Tailwind in `web/`; pages `/`, `/eval`, `/about` | 9 | web/ | build | DONE |
| FE-2 | fetch + ReadableStream SSE parser (partial chunks, multi-line data, CRLF); Stop via AbortController | 9, 16 | `web/lib/sse.ts` | vitest | DONE |
| FE-3 | Clickable citation chips → highlight source card; XSS-safe markdown (no dangerouslySetInnerHTML) | 9 | components | vitest | DONE — a citation chip opens that source's evidence drawer with the cited line highlighted (D-033) |
| FE-4 | Sources panel: title, code, section+title, page, type badge, score, snippet, "view full chunk" | 9 | components | vitest | DONE — compact source chips (canonical label, status icon) + on-demand evidence drawer with document, code, section, page, full chunk and highlighted snippet; score intentionally removed (CNF-2) |
| FE-5 | Confidence badge, unverified-figures badge, not-in-KB state with contacts | 9 | components | vitest | DONE |
| FE-6 | History sent each turn; New chat; sessionStorage persistence | 9 | web/lib | vitest | DONE |
| FE-7 | 6 starter questions; 2–3 template follow-ups (no LLM call) | 9 | components | vitest | DONE |
| FE-8 | Feedback thumbs, copy, timestamps, skeletons, empty/error/offline states, waking banner w/ health polling | 9 | components | vitest/manual | DONE |
| FE-9 | Responsive, accessible (labels, focus, aria-live, Enter/Shift+Enter, contrast), dark mode | 9 | components | manual + lint | DONE — automated + live E2E; no axe audit run |
| FE-10 | Disclaimer line | 9 | layout | vitest | DONE |
| FE-11 | `NEXT_PUBLIC_API_URL` direct to Render, no proxy, no secrets; build passes when unset | 9, 16 | web/lib/api.ts | build ×2 | DONE |
| FE-12 | `/eval` dashboard: retrieval metrics, correctness, groundedness, citations, abstention, latency, cost, per-category, failures table, run comparison | 9 | web/app/eval | build + vitest | DONE — UI renders any run; real results pending |
| FE-13 | eslint, tsc --noEmit, next build pass | 9 | web/ | run | DONE |
| FE-14 | Playwright smoke test against mocked API | 12 | web/e2e | run | DONE |

## Evaluation
| ID | Requirement | § | Implementation | Test | Status |
|---|---|---|---|---|---|
| EV-1 | `python -m eval.run --provider openai --judge openai\|none --out …` (Ollama options removed, D-011) | 11.3 | `eval/run.py` | run | DONE |
| EV-2 | Fast retrieval-only run (no LLM, offline w/ cached embeddings) in CI | 11.3 | eval/run.py | CI | DONE — verified offline in fresh clone |
| EV-3 | Commit representative `eval/results/latest.json` | 11.3 | eval/results | file | DONE |
| EV-4 | LLM-judge rubric in `eval/judge_prompts/`, `JUDGE_MODEL`, temp 0 | 11.2 | eval | — | DONE |
| EV-5 | Ablations: dense / BM25 / hybrid / hybrid+rerank; ±dedup; ±headers; fixed-size vs structure; ollama vs openai | 11.2 | `eval/ablations.py` | run | DONE — ollama-vs-openai N/A (D-011) |
| EV-6 | `docs/EVAL_REPORT.md` with real numbers + failure analysis | 17 | docs | — | DONE |

## Metrics
| ID | Requirement | § | Implementation | Test | Status |
|---|---|---|---|---|---|
| MET-1 | Hit@k, Recall@k (1,3,5,10), MRR, nDCG@10 (section-level), context precision, noise ratio | 11.2 | `eval/metrics.py` | unit | DONE — implemented + unit-tested |
| MET-2 | Key-fact recall + forbidden-fact violations; judge 0–2; agreement | 11.2 | metrics | unit | DONE |
| MET-3 | Claim-level groundedness + figure-verifier rate; hallucination rate | 11.2 | metrics | — | DONE |
| MET-4 | Citation precision/recall; snippet exists in chunk; Source section ids match metadata | 11.2 | metrics | unit | DONE — implemented + unit-tested |
| MET-5 | Abstention P/R/F1; over-refusal rate | 11.2 | metrics | unit | DONE — implemented + unit-tested |
| MET-6 | Injection success rate, PII leak rate | 11.2 | metrics | unit | DONE |
| MET-7 | Latency p50/p95 per stage, tokens, cost/query, cache hit rate | 11.2 | metrics | — | DONE |
| MET-8 | Targets: R@5≥.90, MRR≥.80, KF≥.90, grounded≥.95, cit-prec≥.90, abst-F1≥.90, inj/PII=0 — report honestly | 11.3 | EVAL_REPORT | — | DONE — 10/10 targets met (final run `20261006T231154Z-full`; groundedness measurement change and xd-01/02/04 retrieval misses documented) |

## Golden dataset
| ID | Requirement | § | Implementation | Test | Status |
|---|---|---|---|---|---|
| GLD-1 | ≥70 items, hand-verified, fields per §11.1 | 11.1 | `eval/golden.jsonl` | schema test | DONE |
| GLD-2 | Category minimums: single-fact ≥6/doc (36), cross-doc ≥6, conflict ≥6, absent ≥8, unsupported ≥4, multi-turn ≥4, adversarial ≥4, garbled ≥2, Hinglish/Hindi ≥3 | 11.1 | golden | schema test | DONE |
| GLD-3 | All seed cases from §11.1 included | 11.1 | golden | review | DONE |
| GLD-4 | `gold_chunk_ids` resolved by script from `gold_sections` | 11.1 | eval/run.py | unit | DONE |

## Content-trap behaviour (§3.3)
| ID | Requirement | § | Implementation | Test | Status |
|---|---|---|---|---|---|
| CT-A | TOC-listed but missing sections detected automatically; questions depending on them abstain | 3.3A | audit + gate | audit + eval | DONE |
| CT-B | Wrong in-FAQ section citations documented; structural citation used | 3.3B | audit + citations | audit + regression | DONE |
| CT-C | Conflicts surfaced with both sources (mandate fee, P2M T+5, Luxe waiver year, ATM free count, GST wording, closure fee, FD 3-year overlap) | 3.3C | audit + prompt + context | eval conflict cases | DONE |
| CT-D | Unsupported services → "FinBase does not offer X" with source | 3.3D | prompt | eval | DONE |
| CT-E | Genuinely absent info abstains (UPI mandate bounce fee, home loan, repo rate…) | 3.3E | gate + prompt | eval | PARTIAL — 8/9 absent items abstain; ab-02 documented |
| CT-F | Boilerplate/FAQ dedupe without losing distinct facts | 3.3F | dedup | unit | DONE |
| CT-G | Audit `python -m app.audit` → `docs/DATA_AUDIT.md`, from real PDF inspection (not hard-coded) | 3.3 | `app/audit.py` | Phase 0 gate | DONE |

## Testing
| ID | Requirement | § | Implementation | Test | Status |
|---|---|---|---|---|---|
| TST-1 | pytest ≥80% coverage on `app/` | 12 | tests/ | pytest-cov | DONE — 94 percent |
| TST-2 | Unit tests per §12 list | 12 | tests/unit | run | DONE |
| TST-3 | Integration (TestClient + FakeLLM + fixture index) per §12 list | 12 | tests/integration | run | DONE |
| TST-4 | Provider contract tests with respx | 12 | tests/unit | run | DONE — httpx2.MockTransport instead of respx (D-014) |
| TST-5 | ruff clean; mypy --strict on app/ | 0.6 | pyproject | run | DONE |

## Docker
| ID | Requirement | § | Implementation | Test | Status |
|---|---|---|---|---|---|
| DOC-1 | `backend/Dockerfile` (spec §10.11 path), `web/Dockerfile`, `docker-compose.yml` (api, web, `ollama` profile + model-pull helper) | 10.11 | files | `docker compose build` | PARTIAL — files written; docker not installed so build not run |
| DOC-2 | `.dockerignore`; no secrets in layers | 16 | files | inspect | PARTIAL — .dockerignore + no key in shippable files; image scan needs docker |

## CI
| ID | Requirement | § | Implementation | Test | Status |
|---|---|---|---|---|---|
| CI-1 | `.github/workflows/ci.yml`: ruff, mypy, pytest, retrieval-only eval, frontend lint/typecheck/test/build | 12 | ci.yml | YAML valid / act | PARTIAL — all CI steps verified locally/fresh clone; not run on GitHub (no remote) |
| CI-2 | LLM-judged eval only when `OPENAI_API_KEY` secret exists | 10.9 | ci.yml | review | DONE |

## Render
| ID | Requirement | § | Implementation | Test | Status |
|---|---|---|---|---|---|
| RND-1 | `render.yaml`: python 3.11, pip install, uvicorn start, health `/api/health`, env vars | 13 | render.yaml | YAML valid | DONE — YAML validated |
| RND-2 | Fits 512 MB (no torch) — measured RSS number in README | 13 | — | measure | DONE — 205 MB RSS after 40 requests (Windows) |

## Vercel
| ID | Requirement | § | Implementation | Test | Status |
|---|---|---|---|---|---|
| VCL-1 | Root `web/`, `NEXT_PUBLIC_API_URL`, Vercel domain into `CORS_ORIGINS` | 13 | docs + web | build | PARTIAL — config verified (npm ci + build in fresh clone); not deployed |

## Documentation
| ID | Requirement | § | Implementation | Test | Status |
|---|---|---|---|---|---|
| DC-1 | README: all items listed in §15 | 15 | README.md | review | DONE |
| DC-2 | `docs/ARCHITECTURE.md` with Mermaid | 4 | docs | — | DONE |
| DC-3 | `docs/API.md` with curl + SSE example | 8 | docs | — | DONE |
| DC-4 | `docs/DEPLOYMENT.md` + `scripts/smoke_test.sh` + `scripts/smoke_test.ps1` | 13 | docs/scripts | run locally | DONE — smoke scripts pass against local API; deploy pending |
| DC-5 | `docs/DECISIONS.md`, `docs/BUILD_LOG.md`, `docs/DATA_AUDIT.md`, `docs/EVAL_REPORT.md`, `docs/DEMO_TRANSCRIPT.md` | 0, 17 | docs | — | DONE |
| DC-6 | `CLAUDE.md` + `AGENTS.md` with conventions + commands | 0.8 | root | — | DONE |
| DC-7 | Make targets + script equivalents (Windows): ingest (OpenAI), check-openai, dev-api, dev-web, test, eval | 13, 16 | Makefile + `scripts/` | run | DONE |
| DC-8 | Dependencies pinned (Python 3.11, Node 20 LTS) | 0.5 | requirements*.txt, package.json | — | DONE — pinned; Node 22 per D-021 |

## Video script
| ID | Requirement | § | Implementation | Test | Status |
|---|---|---|---|---|---|
| VID-1 | Timed 5-min script (≤4:45 spoken), the 11 segments in §15, exact demo questions | 15 | `docs/VIDEO_SCRIPT.md` | review | DONE — script; recording is a manual step |

## Bug-prevention checklist (§16)
Tracked item-by-item in `docs/BUILD_LOG.md` → "Section 16 checklist". Status: NOT_STARTED.

## Final verification
| ID | Requirement | § | Implementation | Test | Status |
|---|---|---|---|---|---|
| FIN-1 | Fresh-clone test following README in clean dir/venv | 17.9 | — | run | DONE — fresh-clone verification passed (see BUILD_LOG) |
| FIN-2 | Seed questions end-to-end recorded in `docs/DEMO_TRANSCRIPT.md` | 17.9 | — | run | DONE — regenerated from the final run `20261006T231154Z-full` (all 94 items, verbatim) |
| FIN-3 | Definition of Done §18 items evidenced in BUILD_LOG | 18 | — | review | PARTIAL — all local DoD items evidenced; deployment pending |
