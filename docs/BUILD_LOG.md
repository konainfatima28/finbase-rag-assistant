# Build log

Evidence for every phase gate in `PROMPT.md` §17. Only commands that were actually run are recorded. Output is summarised, with exact numbers copied from the run.

Environment: Windows 11, Python 3.11 venv (`py -3.11 -m venv .venv`), Node v24.13.0, git 2.55. **Not available:** Ollama, Docker, GNU make, `OPENAI_API_KEY` (see DECISIONS D-010).

---

## Phase 0 — Setup & audit (2026-10-06)

### Discovery
- `ls data/raw` → `sample_1.pdf … sample_6.pdf` are present (all six required PDFs; sha256 values in DATA_AUDIT §1).
- PyMuPDF raw-span inspection: the rupee glyph is a **ZapfDingbats** character. PyMuPDF yields `I`, pdfplumber `n`, pdftotext-style tools `■` → D-001.
- Naive FAQ count on `\f`-joined text (`^Q\d{3}:`): loans **86**, cards **98**, savings 98, payments 98, FD 100, KYC 100. Anywhere-regex: **100** for all six. This reproduces the spec's measured 86/98.
- `pip install` of all pinned runtime/dev deps on Python 3.11 succeeded (`requirements*.txt`). FlashRank `ms-marco-MiniLM-L-12-v2` downloaded (21.6 MB) and scored a test pair (0.9998 vs 0.0) in 5.2 s cold.

### Commands & results
| command | result |
|---|---|
| `python -m app.audit` | exit 0, **gate PASS**: faq_exactly_100_per_doc ✔, no_glyph_residual ✔, no_markdown_or_toc_residual ✔, missing_sections_detected ✔, conflicts_detected ✔ (12 findings), suspect_values_detected ✔, all_prompt_expectations_reproduced ✔ (15/15) |
| `ruff check app scripts` | All checks passed |
| `ruff format --check app scripts` | 16 files already formatted |
| `mypy` (strict, `app/`) | Success: no issues found in 22 source files |

### Audit highlights (from `docs/DATA_AUDIT.md`)
- 1172 raw chunks → **190** after dedup. Boilerplate collapses to 1 canonical chunk per doc (14–17 copies each), the three KYC annexes merge into 1, and each FAQ directory goes 100 → 10.
- Missing TOC sections: cards §22, savings §22, payments §22, FD §21, KYC §21 + §22 (loans: none) — matches spec §3.3-A.
- Wrong in-FAQ section citations: loans Q001, Q002, Q003 (§4.2 → actually §6.2) and Q004 (§4.1 → §6.1). The spec named only Q001.
- Conflicts detected by generic detectors: mandate fee ₹150–400 vs ₹150–350; Luxe waiver period wording; GST wording (₹999, ₹4,999, ₹21); savings ATM non-metro omission (Q004); closure fee "at any time" vs bounded table (Q010); P2M/ATM/POS compensation T+5 vs T+2; FD 3-year bucket overlap; contactless ₹5,00,0 row.
- Suspect values flagged: `₹1,00,0` (loans §21) and `₹5,00,0` (savings §4, repeated in every templated copy).
- Unsupported services: crypto, agricultural loans, intraday/F&O tips, chit funds/Ponzi (FD §22).
- Absence probes: home/auto loan, repo/market rates, reward-point value/expiry/redemption, UPI mandate bounce fee amount (payments SOP), tax advice, other banks: all **absent**. The only bounce-fee amount in the corpus is in personal_loans (§5.2, §21, Q005).

### Fixes made during Phase 0
1. `deglue` split TOC entries at `[Section N:` and produced duplicate headings → excluded `[`-preceded matches.
2. Bold FAQ questions reaching the right margin were glued to their answers → added a font-weight block boundary (D-002). Bold detection now ignores the regular-weight `• ` glyph.
3. Audit FAQ-citation check had false positives (cards Q003, loans Q005) → calibrated on the measured score distribution (D-009).
4. Audit multi-line-cell list was hard-coded → now uses `Table.merged_cells`, recorded by the loader.

**Gate: PASS** — requirements understood (`docs/REQUIREMENTS_CHECKLIST.md`), structure created, audit works, missing sections, conflicts and suspect values identified, 100 FAQs per PDF.

Remaining issues carried forward: no embedding/chat model is available locally (Ollama not installed, no OpenAI key). Phases 2–4 will verify against real models only where possible, and will say so explicitly.

---

## Phase 1 — Ingestion (2026-10-06)

Implemented: loader registry (`.pdf` PyMuPDF + pdfplumber, `.docx`, `.md/.txt`, `.csv`, OCR fallback hook), cleaner (traps 1–4 + NFC), table extraction with cross-page merge, section tree + FAQ parser, structure-aware chunker (contextual headers, table + `table_row` chunks, FAQ chunks), deduper with report, `python -m app.ingest --chunks-only`, `python -m app.ingest.inspect --sample`.

| command | result |
|---|---|
| `pytest -q --cov=app` | **96 passed**; coverage **91%** (`__main__`/`inspect` CLIs not yet covered → Phase 2) |
| `python -m app.ingest --chunks-only` (×2) | 6 docs, 152 pages, 600 FAQ items parsed, 1172 → **190** chunks (982 duplicates removed; annex 2, boilerplate 6, faq 60, policy 42, table 7, table_row 73), 4 suspect-value chunks, 27,757 tokens. `chunks.jsonl` sha1 `94c25c6c…` **identical on both runs** |
| `python -m app.ingest.inspect --sample` | 10 chunks inspected (6.2 Foreclosure, loan-cancellation `table_row` flagged `suspect_value`, Luxe 1.2, late-fee `table_row`, payments FAQ Q001 with 9 duplicates, payments boilerplate with 16 duplicates incl. U16/U30/U69/U88 table, savings rate table, FD §22 exclusions, KYC §5, KYC annex with 2 duplicates). All headers/breadcrumbs correct, no glyph/markdown residue, wrapped lines joined |
| `ruff check` | 1 finding: deferred import of `app.ingest.build` in `app/ingest/__main__.py` (module lands in Phase 2) |
| `mypy` | clean before `__main__` referenced the Phase-2 module; re-run at Phase 2 gate |

Tests written per trap (`tests/unit/test_cleaner.py`): `■` → `₹` (digit, `(`, space+digit, stray glyph dropped, never survives), 7 glued-line patterns incl. `\f`, TOC entries untouched, 100-FAQ count on fully glued text, wrapped `(… case\n11)`, font-weight boundary, punctuation fallback, bold/backticks (incl. bold across a wrap), multi-line cells, NFC, truncated values not repaired. Chunk invariants on the real corpus (`tests/unit/test_chunker.py`): full metadata, ≤ max tokens, no `■`/`\f`/`**`/TOC anchors/broken wraps, 100 FAQs per doc before and 10 after dedup, no duplicate normalised text, boilerplate 1/doc, U69 table survives, `table_row` parent exists and contains the row, 6.2 breadcrumb, FD table intro, suspect flags, the Q001 "Section 4.2" stays text-only, reproducibility.

Fix made: an explicit `Section 6.2:` heading with no `Section 6` parent raised `KeyError` (found by a synthetic test). It is now treated as top-level; no parent is invented.

**Gate: PASS** (cleaner/chunker tests pass, sample inspected, `data/processed/dedup_report.json` produced).

---

## Owner decision D-011 — switch to OpenAI-only (2026-10-06)
- Before Phase 2, an attempt to install Ollama (`winget install Ollama.Ollama`) was **rejected by the owner and not executed**. No Ollama or local model was installed or downloaded.
- Changes: settings accept only `openai` (`LLM_PROVIDER`, `EMBED_PROVIDER`); defaults `OPENAI_CHAT_MODEL=gpt-4.1-mini`, `OPENAI_EMBED_MODEL=text-embedding-3-small` (verified against OpenAI's official model, deprecation and pricing pages, D-012); `.env.example`, `config/pricing.yaml`, `scripts/tasks.py`/`Makefile` (`ingest`, `check-openai`; `ingest-ollama` removed), `CLAUDE.md`/`AGENTS.md`, and the checklist (`NOT_APPLICABLE (D-011)` items) updated.
- `OPENAI_API_KEY` check: **not set** (process env, user env, machine env, and no `.env`). Phase 2 is paused at its gate until the key is configured.

---

## Phase 2 — Providers & indexes (2026-10-06) — PARTIAL (blocked on `OPENAI_API_KEY`)

Second owner instruction re-confirmed OpenAI-only. Inspection `grep -ri "ollama|nomic|11434"` over code, config and requirements → **0 hits** (only docs recording the decision). Checklist rows that still read `openai|ollama` were reworded.

Implemented: `app/providers/{base,openai_provider,factory,check}.py` (Responses API + Embeddings API, async, timeouts, retries, `store=False`, temperature fallback), `app/ingest/embed_cache.py` (sqlite), `app/retrieval/bm25.py`, `app/retrieval/store.py` (manifest, validation, FAISS + BM25 load/search), `app/ingest/build.py` (embed + write index), `python -m app.ingest` full path, `python -m app.providers.check`.

| command | result |
|---|---|
| `pytest -q --cov=app` | **119 passed**, coverage **91%** |
| `ruff check app tests scripts` | All checks passed |
| `mypy` (strict) | Success: no issues found in 32 source files |
| `tests/unit/test_openai_provider.py` | 10 contract tests on mocked HTTP: Responses request shape (model, instructions, input, temperature 0, `store=false`, max_output_tokens, json_object), temperature-rejection retry, 5xx/401 → `ProviderError`, SSE deltas + usage, stream error event, embeddings batching/order/normalisation, missing key → actionable error, network guard |
| `tests/unit/test_store.py` | 13 tests with a deterministic **test-only** FakeEmbedder (1536-d): files + manifest fields, FAISS row ↔ chunk mapping incl. FAQ question rows, BM25 same order, self-retrieval, BM25 finds loans §6.2, **startup fails fast** on model/dim/provider/normalisation/tampered-manifest mismatch, partial copy detected, wrong-dim and unnormalised query vectors rejected, rebuild reproducible (same content_hash) with 0 embedding calls (cache), ingest CLI end-to-end |

Incident: the first contract-test run leaked requests with dummy key `sk-test` to api.openai.com (respx cannot patch openai 3.x's `httpx2`). Fixed by D-014, and the guard is verified to block.

**Not executed (needs `OPENAI_API_KEY`):** `python -m app.providers.check` (real model availability), `python -m app.ingest` (real `indexes/openai-text-embedding-3-small/`), dense retrieval smoke queries on real embeddings.

**Gate: NOT YET PASSED** — manifest-mismatch test passes; real-provider and real-retrieval smoke checks are pending the key.

---

## Phase 3 — Retrieval (2026-10-06) — PARTIAL (mechanics verified; quality eval needs key + golden set)

Implemented: `app/safety/pii.py`, `app/cache/ttl_lru.py`, `app/retrieval/{router,fusion,rerank,context,gate,pipeline}.py`. Audit findings now carry structural `members`; ingestion writes `conflicts.json` (multi-member conflict groups) into the index dir; context assembly pulls in the partner chunk of any selected conflict member. `config/thresholds.json` holds **uncalibrated** defaults (`"calibrated": false`) until `eval.calibrate` runs on real embeddings.

| command | result |
|---|---|
| `pytest -q --cov=app` | **174 passed**, coverage **93%** |
| `ruff check app tests scripts` / `mypy` | All checks passed / no issues in 40 files |
| Phase-3 tests | PII (15 masked cases, 6 non-PII left intact incl. ₹ amounts, KB helpline & @finbase.com, idempotent); router (7 domains, multi-label, never a filter); RRF formula; TTL-LRU expiry/eviction/metrics; assembly caps/budget/final_k, table_row → parent table, conflict partner added even when not retrieved, FAQ-duplicate matching; gate scoring/labels/no-reranker redistribution; FlashRank failure → fused order (no retry storm); **real FlashRank model** (local cache, network blocked) ranks the foreclosure passage above an unrelated one; pipeline on the real corpus in all 4 modes (dense/bm25/hybrid/hybrid_rerank) with the test-only embedder: cross-doc query retrieves both payments_upi and savings_account, mandate-fee query surfaces **both** ₹150 to ₹400 (§4.2) and ₹150 - ₹350 (§21), boilerplate down-weighted, retrieval cache hit on normalised repeat |

**Not executed (needs `OPENAI_API_KEY` + Phase-7 golden set):** retrieval-only eval with real embeddings, ablation table, calibrated thresholds.

**Gate: NOT YET PASSED** (mechanics verified; measured quality pending).

---

## Phase 4 — Generation & safety (2026-10-06) — PARTIAL (FakeLLM gate passed; real-model run needs key)

Implemented: `prompts/answer_system.txt` (extracted **verbatim** from PROMPT.md §7.1 by script), `prompts/rewrite_system.txt`, `app/generation/{prompts,rewrite,citations,verifier,confidence,answer}.py`, `app/safety/injection.py`, `app/observability/costs.py`.

| command | result |
|---|---|
| `pytest -p no:cacheprovider` | **217 passed** (85.6 s) |
| `pytest --cov=app` | coverage **93%** |
| `ruff check` / `mypy` | All checks passed / no issues in 48 files |
| `tests/unit/test_generation_parts.py` | verifier: `1.5%`≡`1.50%`, `₹1,00,000`≡`100000`, `5 lakh`≡`₹5,00,000`, `T + 2`≡`T+2`, `Rs 500`≡`₹500`; unsupported `0%` flagged; computed (`= ₹3,500`) and from-question figures; question figures untrusted under injection; truncated-value repair caught while a verbatim garbled quote is allowed; section/FAQ/code/helpline/year tokens ignored. Citations: invalid markers dropped, coverage, **4.2-vs-6.2 regression** (FAQ text says §4.2, sources say §6.2 + FAQ Q001, always with document title, body primary / FAQ secondary, exact `Source:` line), only cited blocks become sources. Injection detection/neutralisation/leak guard. Language detection + rewrite JSON + 3 fallback paths + no call for plain English. Confidence caps |
| `tests/integration/test_answer_service.py` | 15 tests, real corpus + FakeLLM: grounded answer with structural citation and `Answer:`/`Source:` format, verbatim system prompt sent, §7.1 user-turn shape; NOT_FOUND → standard message with KB contacts + related sources; abstain gate skips the LLM; uncited → exactly one stricter retry; invalid `[42]` dropped; guessed `₹1,00,000` cancellation fee replaced by "unclear in source"; system-prompt leak blocked; injected `0%` flagged unverified; card number + OTP never sent to the LLM; LLM down → degraded result with sources; answer cache; follow-up rewrite with history as prior turns (never in CONTEXT); SSE event order meta→token*→sources→verification→done; NOT_FOUND never streamed; error event on provider failure |

**Not executed (needs `OPENAI_API_KEY`):** the manual run of the seed questions against the real OpenAI model (the spec said Ollama; changed per D-011).

**Gate: PARTIAL** — FakeLLM integration tests and NOT_FOUND pass; the real-model seed run is pending the key.

---

## Phase 5 — API (2026-10-06) — PASS for everything testable offline; deployed-URL SSE check pending

Implemented: `app/main.py` (factory, lifespan fail-fast, CORS exact origins without credentials, pure-ASGI request-id/body-size/access-log middleware, 422/429/500 JSON handlers without stack traces), `app/api/{schemas,services,routes,sse}.py`, `app/observability/{logging,metrics}.py` (structlog JSON with PII-redaction processor and secret-key scrubbing; p50/p95 per stage, cache hit rates, abstain/error/degraded rates, tokens, cost).

| command | result |
|---|---|
| `pytest -p no:cacheprovider --cov=app` | **238 passed**, coverage **94%** (218.6 s) |
| `ruff check` / `ruff format --check` / `mypy` | passed / 75 files formatted / no issues in 55 files |
| `tests/integration/test_api.py` (21 tests) | non-stream chat contract; SSE headers (`no-cache`, `X-Accel-Buffering: no`) + event order; history follow-up; server-side session memory; not-found; injection doesn't leak the prompt; empty/whitespace → 422; 2,001 chars → 422; >64 KB body → 413; rate limit `3/minute` → `[200,200,200,429]`; health/ready/`/docs`/openapi; CORS preflight allowed only for the exact origin, no credentials header; provider failure → SSE `error` + `done` and degraded JSON; unhandled exception → 500 JSON with no traceback/internal text; docs/list + chunk + 404; feedback JSONL with PII redacted and invalid rating → 422; metrics; eval latest/runs; startup fails fast without an index; SSE frame format; heartbeat while idle + **client disconnect cancels the upstream generator**; upstream exception → `error` event |
| `uvicorn app.main:app` (no index present) | `IndexNotFoundError: No index at …\indexes\openai-text-embedding-3-small. Build it with python -m app.ingest (needs OPENAI_API_KEY).` → `Application startup failed. Exiting.` (fail-fast verified on a real process) |

**Not executed:** a live server answering real questions (needs the OpenAI index → key), and SSE through the deployed Render URL (Phase 8/9).

**Gate: PASS** (integration + SSE tests pass, `/docs` renders); live checks carried to Phase 9.

---

## Phase 6 — Frontend (2026-10-06) — PASS (against mocked API; manual E2E against a live API pending the key)

Stack (pinned, verified on npm 2026-10-06): Next.js 16.3.8 (App Router, Turbopack), React 19.3.0, TypeScript 5.9.3, Tailwind 4.3.3, ESLint 10.12.0 + eslint-config-next 16.3.8 (flat config), Vitest 5.0.3 + Testing Library, jsdom 28.1.0, Playwright 1.63.0. Node target **22 LTS** (`.nvmrc`; Node 20 reached end-of-life in April 2026, and vitest 5 requires Node ≥ 22.12 — D-021).

Implemented: chat (`fetch` + `ReadableStream` SSE parser, Stop via `AbortController`, streaming render with `aria-live`, XSS-safe renderer with no `dangerouslySetInnerHTML`, enforced by the ESLint `react/no-danger` error rule), citation chips that scroll to and highlight the source card, sources panel (title, code, section + title, page, type badge, relevance, highlighted snippet, "view full source" drawer via `/api/chunks/{id}`), confidence/unverified-figures/conflict/no-citation badges, not-in-KB card with KB contacts + related topics, history sent each turn + server session id, `sessionStorage` persistence, New chat, 6 starters, template follow-ups (no LLM call), thumbs feedback, copy, timestamps, skeletons, API-waking banner with health polling and automatic resend, offline/error/unconfigured states, PII notice, disclaimer, dark mode (pre-paint script + `useSyncExternalStore`), skip link, focus rings, reduced motion. `/eval` dashboard (target tiles, retrieval/answer bars, latency & cost, ablation small multiples, per-category table, failed cases, run comparison). `/about` (pipeline + KB coverage). Chart color `#2a78d6`/`#3987e5` validated with the dataviz palette validator (lightness band, chroma, ≥3:1 contrast on both surfaces: ALL CHECKS PASS).

| command | result |
|---|---|
| `npm run lint` (eslint .) | exit 0 |
| `npm run typecheck` (tsc --noEmit, strict + noUncheckedIndexedAccess) | exit 0 |
| `npm run test` (vitest) | **21 passed** (3 files): SSE parser (split at every byte position, multi-line data, CRLF incl. CR/LF split across reads, heartbeats, flush, UTF-8 `₹` split mid-code-point), RichAnswer (chips only for valid markers; `<img onerror>`/`<script>` rendered as text), SourcesPanel fields, AnswerCard states, Composer keys/Stop/blank, history helpers, BarList, ChatApp with mocked fetch (stream → follow-up sends history; unconfigured banner) |
| `npm run build` with `NEXT_PUBLIC_API_URL` **unset** | exit 0 (UI shows the configuration banner) |
| `npm run build` with `NEXT_PUBLIC_API_URL=https://finbase-api.onrender.com` | exit 0; URL inlined in client chunks; bundle grep for `sk-…`/`OPENAI_API_KEY` → none |
| `npx playwright test` (production build, mocked API) | **2 passed**: starter question → streamed answer → High confidence → citation chip → source drawer → Esc; no page errors. Waking banner when health fails |

Bugs found and fixed:
1. **Production crash found by Playwright** ("This page couldn't load", `destroy is not a function`). Current Chrome's `scrollIntoView()` returns a Promise, and an expression-bodied `useEffect` returned it as its "cleanup". jsdom returns `undefined`, so unit tests missed it. Fixed by giving all effects block bodies; the smoke test now asserts zero page errors.
2. ESLint 10 broke `eslint-plugin-react` version auto-detection (removed `context.getFilename`) → explicit `settings.react.version`.
3. React-hooks rules flagged setState-in-effect (theme toggle, source drawer) → `useSyncExternalStore` / keyed remount.

**Not executed:** manual end-to-end against the local API with real OpenAI answers (needs the index → key).

**Gate: PASS** for lint, typecheck, tests, both builds and the mocked E2E. Live E2E is carried to Phase 9.

---

## Key check before the real OpenAI work (2026-10-06)
The owner reported `OPENAI_API_KEY` as configured. Verification without printing the key: `Settings().has_openai_key` → **False**. There is no `.env` file in the project root (only `.env.example`, whose `OPENAI_API_KEY` is empty), and no process/user/machine environment variable. The OpenAI-dependent steps (index build, real retrieval/generation, full eval, calibration, latency/cost, memory) were therefore **not executed**. Everything else continued.

## Phase 7 — Evaluation (2026-10-06/07) — PARTIAL (offline parts run; OpenAI parts blocked by the missing key)

Implemented: `eval/golden.jsonl` (94 items), `eval/golden.py` (schema + coverage validation, gold chunk resolution), `eval/metrics.py`, `eval/judge.py` + `eval/judge_prompts/` (copies in `prompts/judge_*.txt`, identity tested), `eval/run.py` (retrieval-only/full, 4 modes, targets, per-category, failures; offline query-embedding cache), `eval/calibrate.py` (grid search, F1 with over-refusal ≤ 5%), `eval/ablations.py` (structure vs fixed-size, ±dedup, ±headers), `eval/transcript.py`.

| command | result |
|---|---|
| golden validation (script + `tests/unit/test_eval.py`) | 94 items, 0 schema problems; every gold section resolves to indexed chunks; every expected fact of answerable single-fact/cross-doc/conflict/multi-turn/Hinglish items is present in its gold sections' text |
| `pytest tests/unit/test_eval.py` | 11 passed (metric definitions, gold matching, any/all recall, normalised key facts, forbidden facts, abstention, citation metrics, targets, calibration constraint, transcript) |
| `python -m eval.ablations` (BM25, offline) | default recall@5 **0.940**, MRR **0.845**, nDCG@10 **0.899**, hit@1 **0.738**; no-dedup 0.726/0.762/0.726/0.726; no-headers 0.935/0.785/0.856/0.643; fixed-size 0.262/0.263/0.249/0.214 (84 items) |
| `python -m eval.run ...`, `python -m eval.calibrate` | **not run** — need the OpenAI index (key) |

**Gate: NOT PASSED** — `docs/EVAL_REPORT.md` contains real offline numbers. Full metrics, calibration and failure analysis are pending the key.

## Phase 8 — Docker, CI, deploy files, docs (2026-10-07) — PARTIAL

Written: `backend/Dockerfile` (python:3.11-slim, non-root, FlashRank baked in, index copied, key only at runtime), `web/Dockerfile` (Node 22 standalone, non-root), `docker-compose.yml` (api + web; no Ollama profile per D-011), `.dockerignore` (root + web), `render.yaml`, `.github/workflows/ci.yml` (ruff, format, mypy, audit, pytest with `--cov-fail-under=80`, offline retrieval eval when the index exists, LLM eval only when the secret exists; frontend lint/typecheck/test/both builds/Playwright), `scripts/smoke_test.sh` + `.ps1`, `scripts/warm_reranker.py`, `README.md` (env table generated from the `Settings` model: 54 variables + `NEXT_PUBLIC_API_URL`), `docs/ARCHITECTURE.md`, `docs/API.md`, `docs/DEPLOYMENT.md`, `docs/EVAL_REPORT.md`, `docs/VIDEO_SCRIPT.md`, `docs/DEMO_TRANSCRIPT.md` (pending stub).

| command | result |
|---|---|
| YAML parse of `render.yaml`, `docker-compose.yml`, `.github/workflows/ci.yml` | valid; render `OPENAI_API_KEY` is `sync: false`; health path `/api/health` |
| `bash -n scripts/smoke_test.sh`; PowerShell `Parser::ParseFile(smoke_test.ps1)` | 0 syntax errors |
| `docker compose build` | **not run** — Docker is not installed on this machine |
| CI run on GitHub | **not run** — no remote/commit yet (CI config validated as YAML only) |
| `pytest -p no:cacheprovider --cov=app` | **249 passed**, coverage **94%** |
| `ruff check app eval tests scripts`, `mypy` (strict, app + eval) | passed; no issues in 66 files |
| secret scan (regex for `sk-` + 20 chars, excluding dependencies) | none; `git check-ignore .env` → ignored |

**Gate: PARTIAL** — config files are written and statically valid. `docker compose build` and the CI run were not executed (no Docker, no remote). Real-run numbers in README/EVAL_REPORT are marked pending.

## Section 16 bug-prevention checklist (status as of 2026-10-07)

| item | status | evidence |
|---|---|---|
| Embedder in manifest == runtime; dim matches; separate dirs; fail fast | PASS (tests) | `test_store.py` mismatch/dim/normalisation/tamper tests; real uvicorn fail-fast without an index |
| Vectors normalised for index and query; query/doc prefixes | PASS / N/A | normalisation asserted at build and query time; prefixes N/A (OpenAI, D-011) |
| FAISS ids ↔ chunk ids; BM25 and FAISS same list/order | PASS | `test_faiss_ids_map_to_chunks_and_bm25_shares_order` |
| No `■`, form feeds, markdown, TOC anchors in chunks | PASS | `test_chunks_are_clean` + audit gate |
| Exactly 100 FAQs per PDF before dedupe; unique retained | PASS | `test_exactly_100_faqs_before_dedupe_and_10_unique_after` |
| Citations structural; 4.2-vs-6.2 regression test | PASS | `test_citations_are_structural_regression_4_2_vs_6_2` |
| Section numbers always paired with document title | PASS | citation labels + test |
| Garbled values never repaired | PASS (tests) | verifier + truncation guard tests; real-model behaviour pending eval |
| No hard doc filter; cross-doc retrieves both docs | PASS | `test_cross_document_question_retrieves_both_docs` |
| Over-refusal ≤ 5% on answerable golden items | PENDING | needs real eval |
| SSE headers, heartbeat, abort, browser parser (split/multi-line/CRLF) | PASS locally; Render PENDING | API + vitest + Playwright tests; deployed-URL check pending |
| No blocking calls in async handlers; no unlocked global mutable state | PASS (review + design) | retrieval via `asyncio.to_thread`; caches/metrics/sessions use locks |
| Timeouts on external calls; idempotent retries; friendly errors | PASS | provider timeouts/retries; degraded-path tests |
| CORS exact origins; preflight; no `*` with credentials | PASS | `test_cors_exact_origins` |
| No secrets in repo/logs/bundle/image layers; `.dockerignore` | PASS (repo, bundle, logs); image PENDING (no Docker) | grep scans; redaction processor; `.dockerignore` excludes `.env` |
| History capped; long messages truncated; empty → 422 | PASS | API tests |
| Windows path/encoding safe; `make` script equivalents | PASS | developed and run on Windows; `scripts/tasks.py` |
| `next build` with `NEXT_PUBLIC_API_URL` unset and set | PASS | both builds exit 0 |
| Ingest twice → identical chunk ids and manifest hash | PASS | byte-identical `chunks.jsonl`; `test_rebuild_is_reproducible_and_uses_cache` |
| Injection in question or retrieved chunk doesn't change behaviour | PASS (deterministic layers, FakeLLM); real model PENDING | injection tests; adversarial eval items pending |
| Cold start: first request after restart succeeds | PENDING | needs deployment |

---

## OpenAI key available (2026-10-07) — real OpenAI-dependent phases

Key check without printing it: `Settings().has_openai_key` → **True**. `.env` is git-ignored, untracked, and was never modified by the agent.

### Phase 2 gate (completed)
| command | result |
|---|---|
| `python -m app.providers.check` | `gpt-4.1-mini` available, `text-embedding-3-small` available, embedding ok (dim=1536), chat ok (`gpt-4.1-mini-2025-04-14`, 19+2 tokens) |
| `python -m app.ingest --embedder openai` | 190 chunks, **250 vectors**, dim 1536, normalized; 250 cache misses; est. embedding cost **$0.000583**; 24 s |
| re-run of ingest | **250 cache hits, 0 misses (0 API calls), identical content_hash** `a7c0497b…` |
| retrieval smoke (9 queries, real embeddings + reranker) | correct top section for all 8 answerable queries (loans 6.2, cards 1.2, payments FAQ Q008/U69, FD §1, KYC §3, savings §3, FD §22 for Bitcoin, loans §21 + §4.2 for the mandate conflict). Home-loan query: confidence 0.75, not abstained at the gate (left to `NOT_FOUND`) |
**Gate: PASS.**

### Phase 3/4/7 — calibration, evaluation, failure analysis (5 full runs + targeted runs)
| command | result |
|---|---|
| `python -m eval.calibrate` | gate F1 0.571 → **0.667** (P 1.0, R 0.5, over-refusal 0), weights rerank .60 / dense .25 / gap .05 / lexical .10, threshold 0.22 → `config/thresholds.json` (`calibrated: true`) |
| `python -m eval.run --provider openai --judge openai` run 1 `184404Z` | 4 targets missed; judge produced no scores (JSON-mode 400 from the live API) |
| runs 2–4 | fixes per `docs/EVAL_REPORT.md` §4 (11 system defects, 3 metric defects) |
| **final run `20261006T193632Z-full`** (also `latest.json`) | **10/10 targets met**: Recall@5 0.982, MRR 0.937, nDCG@10 0.949, key-fact recall 1.000, judge 1.83/2, groundedness 0.961, citation precision 0.963 / recall 0.988, abstention F1 0.947 (P 1.0 R 0.9), over-refusal 0.0, injection 0.0, PII leak 0.0; p50 2.9 s, p95 5.2 s, $0.0008/query; judge cost $0.059 |
| retrieval modes (same run) | dense 0.940 / BM25 0.935 / hybrid 0.958 / hybrid+rerank 0.982 recall@5 |
| `python -m eval.ablations --dense` | dedup: dense recall@5 0.304 → 0.940, hybrid+rerank 0.655 → 0.982; headers: dense hit@1 0.821 → 0.893; fixed-size hybrid+rerank 0.363 |
| reranker comparison (retrieval-only) | MiniLM 0.982 / 0.937 @ 1,048 ms p50; TinyBERT 0.970 / 0.915 @ 41 ms; none 0.958 / 0.933 |
| `python -m eval.perf` | uncached 3.5 s / $0.000839; cache hit 0.1 ms / $0; gate abstain 1.4 s / $0; follow-up with rewrite 4.3 s / $0.001166 |
| demo run + `python -m eval.transcript` | `docs/DEMO_TRANSCRIPT.md`: 24 seed questions, verbatim output |
Remaining failures (documented, not hidden): ab-02 (ambiguous UPI AutoPay vs loan EMI mandate; the answer now carries an attribution note) and xd-01/02/04 ranking (answers pass). **Gates: PASS** (Phase 3 retrieval targets met; Phase 4 real-model seed run done; Phase 7 targets met with failure analysis).

### Phase 5/6 live checks
| command | result |
|---|---|
| `uvicorn app.main:app` + `scripts/smoke_test.sh` | PASSED: health (openai, 190 chunks), grounded answer citing §6.2, abstention, SSE (145 events) |
| `scripts/smoke_test.ps1` | PASSED |
| memory, 40 real chat requests (after D-027) | RSS 168 MB at startup → 205 MB after 40 requests (before the fix: 849 MB after 6 reranks); 0 errors; no key in the server log |
| `E2E_LIVE_API=http://127.0.0.1:8000 npx playwright test` (new `e2e/live.spec.ts`) | **1 passed**: real UI + real API: grounded answer with source card and confidence, follow-up resolved to 1.5%, not-in-KB card, 0 page errors (first attempt blocked by CORS because the test origin `:3100` was not allow-listed — expected; re-run with it allowed) |

### Phase 8 — Docker, CI, deploy files, docs
- `render.yaml`, `backend/Dockerfile` and CI now call `python -m app.retrieval.warm` (see Phase 9 fix 1). YAML validated.
- `docker compose build`: **not run** (Docker not installed). CI: **not run on GitHub** (no remote/commit). README, EVAL_REPORT, ARCHITECTURE, DEPLOYMENT, DECISIONS (D-024…D-028) and VIDEO_SCRIPT (no placeholders left) are updated with real numbers.

### Phase 9 — fresh-clone verification
Method: copy exactly `git ls-files -co --exclude-standard` (197 files, no `.env`/venv/node_modules) to an empty temp dir and follow the README.

Round 1 found 3 real defects:
1. `python scripts/warm_reranker.py` → `ModuleNotFoundError: app`. **This would have failed the Render build** → moved to `python -m app.retrieval.warm`.
2. mypy: untyped `onnxruntime` import (introduced with D-027; I had not re-run mypy) → added to mypy overrides.
3. Two files were not formatted.

Round 2 (fresh copy, new venv) also found 1 real defect: **`npm ci` failed** (`package-lock.json` out of sync: missing optional `@emnapi/*`), which would break Vercel and CI → lockfile regenerated, verified with a clean `npm ci`.

Final round-2 results:

| step (fresh clone) | result |
|---|---|
| `py -3.11 scripts/tasks.py setup` | exit 0 (53 s) |
| lint / format / mypy | exit 0 / 87 files formatted / no issues in 68 files |
| `python -m app.audit` | gate PASS |
| `python -m app.retrieval.warm` | downloads + warms the model, exit 0 (as a Render build would) |
| `pytest` | **259 passed**, coverage **93%** |
| `python -m eval.run --retrieval-only` with **no key** | recall@5 0.982, MRR 0.937, nDCG@10 0.949, **0 query-cache misses** (offline CI path works) |
| real API + `scripts/smoke_test.sh` (key provided as local `.env`, deleted afterwards) | PASSED; health: openai, gpt-4.1-mini, text-embedding-3-small, reranker loaded, 190 chunks, gate calibrated |
| web: `npm ci`, lint, typecheck, vitest, build (unset + set), Playwright | all exit 0; 21 tests; 2 passed |

Final security scan: the actual key value is absent from all 197 shippable files, the logs and the built web bundle; `sk-` pattern absent; `.env` ignored and untracked.

**Gate: PASS for local and fresh-clone verification.** Not done: deployment to Render/Vercel, CI on GitHub, `docker compose build`, live cold-start check, video.

## Section 16 bug-prevention checklist — final (2026-10-07)
| item | status | evidence |
|---|---|---|
| Embedder in manifest == runtime; dim; separate dirs; fail fast | PASS | tests + real manifest (openai/text-embedding-3-small/1536/normalized) + uvicorn fail-fast |
| Vectors L2-normalised for index and query; prefixes | PASS / N/A | manifest `normalized: true`; query check; prefixes N/A (D-011) |
| FAISS ids ↔ chunk ids; BM25 and FAISS same list | PASS | tests |
| No `■`, form feeds, markdown, TOC anchors | PASS | tests + audit |
| 100 FAQs per PDF before dedupe; unique retained | PASS | tests + audit |
| Structural citations; 4.2-vs-6.2 regression | PASS | test + real transcript pl-01 cites §6.2/§21/FAQ Q001, never §4.2 |
| Section numbers always with document title | PASS | every `Source:` line in DEMO_TRANSCRIPT |
| Garbled values never repaired | PASS | gv-01 replaced by "unclear" (guard), gv-02 flagged; tests |
| No hard doc filter; cross-doc retrieves both | PASS | test + doc-coverage; xd items' answers pass |
| Over-refusal ≤ 5% on answerable golden items | PASS | **0.0** (final run) |
| SSE headers, heartbeat, abort, browser parser | PASS locally (API tests, vitest, Playwright, live E2E); Render PENDING | — |
| No blocking calls in async handlers; no unlocked shared state | PASS | design + tests |
| Timeouts on external calls; idempotent retries; friendly errors | PASS | provider + degraded tests |
| CORS exact origins; preflight; no `*` with credentials | PASS | test; live E2E blocked as expected until the origin was allowed |
| No secrets in repo/logs/bundle/image | PASS (repo, logs, bundle); image PENDING (no Docker) | real-key scan |
| History capped; long messages truncated; empty → 422 | PASS | API tests |
| Windows path/encoding safe; make equivalents | PASS | built and verified on Windows; `scripts/tasks.py` |
| `next build` with and without `NEXT_PUBLIC_API_URL` | PASS | fresh clone |
| Re-ingest → identical chunk ids + manifest hash | PASS | real re-ingest: 0 API calls, identical hash |
| Injection in question or chunk doesn't change behaviour | PASS | injection success 0.0 (real); context neutralisation test |
| Cold start: first request after restart succeeds | PARTIAL | local restart + immediate request succeeds; Render cold start not testable before deployment |

---

## Frontend hydration warning diagnosis (2026-10-07) — frontend only, backend/RAG untouched

Report: React hydration mismatch at `SiteHeader.tsx` `<Landmark>` with `data-darkreader-inline-stroke` / `--darkreader-inline-stroke`.

**Diagnosis**
- Code review of every SSR'd client component: the theme is applied before paint on `<html>` (the only `suppressHydrationWarning`, scoped to that element). `ThemeToggle` uses `useSyncExternalStore` with server snapshot `false`, so server and hydration render identical markup. `ChatApp` reads `sessionStorage` only in an effect; Dates and random ids are never rendered during hydration. No nondeterministic initial render found.
- The dev-server log diff (`web/.next/dev/logs/next-development.log`) shows **only** Dark Reader attributes added to lucide SVGs; nothing else differs.
- New `web/e2e/hydration.spec.ts`, run in clean Playwright Chromium (no extensions) against the user's own `next dev` on :3000 (`E2E_DEV=1 E2E_BASE_URL=http://localhost:3000`): `/`, `/eval`, `/about` × light/dark, plus a saved theme + restored conversation → **0 console errors/warnings, 0 hydration warnings**.
- A control test injects Dark Reader-style SVG mutation before hydration → the same mismatch is reported. The detector works, and the warning is reproduced only by the extension behaviour.
- **Conclusion: extension-induced. No application code was changed to accommodate it.**

**Separate real bug found by the restored-conversation check (not hydration):** `ChatApp` saved the initial empty message list before loading `sessionStorage`. Under React StrictMode (dev) the re-run load then read `[]`, so a refresh lost the conversation. Fix: a `restored` flag, so nothing is saved before the load. A vitest regression test (StrictMode) **fails without the fix and passes with it**.

**Files changed:** `web/components/ChatApp.tsx`, `web/components/ChatApp.test.tsx`, `web/e2e/hydration.spec.ts` (new), `web/playwright.config.ts` (`E2E_BASE_URL` option).

| command | result |
|---|---|
| `npm run lint` / `npm run typecheck` | exit 0 / exit 0 |
| `npm test` | 22 passed (3 files) |
| `npm run build` | exit 0 |
| `E2E_DEV=1 E2E_BASE_URL=http://localhost:3000 npx playwright test e2e/hydration.spec.ts` | 8 passed (incl. control) |
| `npx playwright test` (production build) | 9 passed, 2 skipped (live-API spec needs `E2E_LIVE_API`; control is dev-only) |

---

## Post-review Phase 1 — RAG, retrieval and evidence quality (2026-10-07)

Scope: fixes to RAG, retrieval, query rewriting, answer generation and evidence quality only. Models unchanged (`gpt-4.1-mini`, `text-embedding-3-small`); FAISS + BM25 + RRF + FlashRank + OpenAI architecture unchanged; no frontend redesign (one null-guard for `relevance`); no deployment changes. Decisions: D-029, D-030, D-031 in `docs/DECISIONS.md`.

**Fixed**
1. Roman Hinglish: the rewrite is triggered by vocabulary (every word is common English or a corpus word), not by script; broad questions get ≤ 3 sub-queries from the same cheap call; clear English is never paraphrased.
2. Grounding in code: an answer still uncited after one stricter retry becomes the standard not-found reply; pipeline words (CONTEXT, blocks, NOTES, NOT_FOUND, system prompt) are scrubbed; an inline `NOT_FOUND` becomes "not available" (not deleted).
3. Truncated values: `unclear_value` evidence status, located per row (column + row label), never shown; confidence Low; explicit "cannot be safely determined".
4. Conflicts: `conflicting_sources` decided in code (both sides in context and the question's best-matching line is the conflicted item); both values enforced with citations; same-document wording; confidence ≤ Medium; distinct from `unclear_value`.
5. Per-row source quality: a table is `suspect_value` only when a row the request is about is incomplete (`unclear_rows`).
6. Broad / cross-document questions: `Retriever.retrieve_many` (main query + sub-queries, interleaved, de-duplicated, ≤ 8 blocks / 3,800 tokens).
7. Duplicates: a retrieved table row is replaced by its table; content already in the context is skipped.
8. FAQ: orphan FAQ questions resolve to the full entry; `(Operational case N)` labels removed from LLM text and snippets; FAQ snippets show question + answer.
9. Related topics after abstention only when every content term of the question is covered; none for injection attempts.
10. `relevance` is null; raw `scores.rerank` / `scores.rrf` exposed (presentation deferred to Phase 3).
11. Found by the real evaluation runs and fixed: false conflict note on unrelated loan questions (shared word "mandate"/"disbursal"); ab-02 product transfer (a personal-loan ₹500 EMI bounce fee given as the UPI AutoPay fee → `product_scope_mismatch` abstention); future-year / missing-section questions now open with "not available"; a `₹` in a log line crashed requests on a cp1252 console (log now records counts only).

**Final evaluation — run `20261006T224229Z-full`** (`python -m eval.run --provider openai --judge openai`, 94 golden items, also written to `latest.json`)

| metric | final | previous full run `221128Z` | pre-Phase-1 `193632Z` | target | |
|---|---|---|---|---|---|
| Recall@5 | 0.982 | 0.982 | 0.982 | ≥ 0.90 | MET |
| MRR | 0.937 | 0.937 | 0.937 | ≥ 0.80 | MET |
| nDCG@10 | 0.949 | 0.949 | 0.949 | ≥ 0.80 | MET |
| Key-fact recall | 1.000 | 1.000 | 1.000 | ≥ 0.90 | MET |
| Groundedness | 0.952 | 0.959 | 0.961 | ≥ 0.95 | MET |
| Citation precision | 0.967 | 0.956 | 0.963 | ≥ 0.90 | MET |
| Abstention F1 | 1.000 (P 1.0, R 1.0) | 0.889 | 0.947 | ≥ 0.90 | MET |
| Over-refusal | 0.000 | 0.000 | 0.000 | ≤ 0.05 | MET |
| Injection success | 0.000 | 0.000 | 0.000 | = 0 | MET |
| PII leak | 0.000 | 0.000 | 0.000 | = 0 | MET |

Other: citation recall 0.994, judge 1.82/2, forbidden-fact violations 0, figure-verified rate 1.0, $0.0009/query, judge cost $0.059.
Failed cases (3, all retrieval-only, answers pass, known before Phase 1): xd-01, xd-02, xd-04 (recall@5 = 0.50: one of the two documents is outside the top 5 of the main-query ranking).

**Regression observed, not addressed in Phase 1:** latency p50 2.9 s → 6.1 s (p95 5.2 s → 13.0 s), almost all in the rerank step (p50 1.4 s → 4.0 s); generation unchanged (~1.3 s). Likely cause (not yet measured in isolation): sub-query reranks for broad/Hinglish questions plus the serialised reranker lock under the evaluator's concurrent requests. Latency is not one of the PROMPT.md target metrics, but it needs a follow-up measurement.

| check | result |
|---|---|
| backend `pytest` | 312 passed (259 before Phase 1; new: `tests/unit/test_phase1_evidence.py`, Phase-1 tests in `tests/integration/test_answer_service.py`, `test_eval_driven_fixes.py`, `test_generation_parts.py`, `test_retrieval_parts.py`) |
| `ruff check` / `ruff format --check` / `mypy` (strict) | pass / 88 files formatted / no issues in 69 files |
| web lint / typecheck / vitest / build / Playwright (run earlier in Phase 1, before the last backend-only changes) | pass / pass / 23 passed / pass / 9 passed, 2 skipped |
| 7 manual scenarios (real OpenAI, current code, one at a time with a 150 s timeout) | all pass: Hinglish answered from KYC §2/FAQ Q006; contactless `unclear_value`, Low, ₹25,000 kept; mandate fee `conflicting_sources`, Medium, both ranges, same document; home loan and injection → not found with no related topics; broad savings → savings §1 + KYC §1/§2/§3; FAQ Q008 with question + answer |
| abstention cases ab-01…ab-09, ad-01 (one at a time, 180 s timeout) | 10/10 abstained |

**Gate: Phase 1 PASS** — all 10 PROMPT.md evaluation targets met. Phase 2 not started.

---

## Post-review Phase 2 — evidence & citation architecture (2026-10-07)

Scope: data / contract / logic layer only. Phase 1 frozen and untouched: same retrieval and context; prompts and the model input are byte-identical. Models unchanged. No UI redesign; frontend changes are type additions and one test-fixture update. No deployment changes. Decision: D-032.

**What changed**
- New `app/generation/canonical.py`, run after all Phase-1 post-processing:
  - logical evidence identity: `<doc>:section:<id>` / `<doc>:faq:<Qnnn>`. A row and its table, split chunks of a section, and an orphan FAQ question with its full entry each become ONE item. Different sections are never merged.
  - citation numbers 1..k in first-citation order; answer markers rewritten (a run like `[2][3]` that points at one item becomes `[1]`).
  - per-item `status` (`normal` / `unclear_value` / `conflicting_sources`), `unclear_rows` (row-level), `conflict_ids`, product `scope`, human `label` from real section titles (e.g. `Personal Loans — Section 4.2: Upfront Processing Charges`, `Personal Loans — FAQ Q008`), `faq_question`, `chunk_ids`.
  - conflict records gain `conflict_id`, `evidence_ids`, citation numbers, `scope` (same/cross document) and a deterministic description.
  - `evidence.claims`: each cited sentence → citation numbers → evidence ids.
- Representative chunk of an item: the full FAQ entry over an orphan question, the table over a row. Found by a Phase-2 unit test: retrieval order could otherwise pick the orphan.
- `relevance` and raw `scores` removed from the public evidence contract (debug scores stay in `meta`).
- Related topics use the same item shape, de-duplicated, never another product's evidence when the question names a product, and none after `product_scope_mismatch`.
- `citations.py`: `Source` / `build_sources` / `raw_scores` removed (replaced by the canonical builder).
- Eval harness: one source may span several chunks. Citation precision counts each logical source once, and the groundedness judge receives all chunks of every cited source.
- Docs: `docs/API.md` (evidence contract), `docs/ARCHITECTURE.md` (step 9b), `docs/DECISIONS.md` (D-032).

**Tests added**
- `tests/unit/test_phase2_canonical.py` (15): identity/labels, stable numbering (citation order, retrieval-order independence, repeatability), table+row and split-section de-dup, duplicate FAQ → one canonical item, unclear value only on the malformed row (neighbouring complete row stays `normal`, answer quoting the malformed value, uncited unclear evidence kept unnumbered), conflict keeps both items with same-document label, cross-document identity and cross-document conflict scope, product scope + related-topic filtering, no scores/percentages, claims → real evidence ids, marker renumbering.
- `tests/integration/test_answer_service.py` (+6, real corpus, test-only FakeLLM): a contract check (contiguous numbers = `citations_valid` = markers, one item per `evidence_id`, claims ⊆ items) on mandate conflict, contactless unclear row, table/row citations, FAQ, cross-document; abstentions (home loan, injection, UPI AutoPay product mismatch) carry no evidence and no related topics.
- Updated: Phase-1 tests asserting `relevance is None` now assert the field is absent; ported `build_sources` tests to `build_evidence`.

| check | result |
|---|---|
| backend `pytest` | **333 passed** (312 after Phase 1) |
| `ruff check` / `ruff format --check` | pass / 90 files formatted |
| `mypy` (strict) | no issues in 70 files |
| web `npm run typecheck` / `npm run lint` | pass / pass |
| web `vitest components/components.test.tsx` (fixture updated) | 13 passed |

**Phase 1 baseline: preserved.** Real-OpenAI evaluation `20261006T231154Z-full` (`--no-latest`, so `latest.json` stays the Phase 1 baseline `20261006T224229Z`):

| metric | Phase 2 | Phase 1 baseline | target |
|---|---|---|---|
| Recall@5 / MRR / nDCG@10 | 0.982 / 0.937 / 0.949 | 0.982 / 0.937 / 0.949 | ≥ 0.90 / 0.80 / 0.80 — MET |
| Key-fact recall | 1.000 | 1.000 | ≥ 0.90 — MET |
| Groundedness | 0.977 | 0.952 | ≥ 0.95 — MET |
| Citation precision | 0.971 | 0.967 | ≥ 0.90 — MET |
| Abstention F1 | 1.000 | 1.000 | ≥ 0.90 — MET |
| Over-refusal / Injection / PII | 0 / 0 / 0 | 0 / 0 / 0 | MET |

Failed cases: xd-01, xd-02, xd-04 only (retrieval recall@5 = 0.50; the same known misses as Phase 1). The groundedness increase is partly a measurement change (the judge now sees every chunk of each cited logical source) and should not be read as a pure quality gain. Latency p50 4.8 s / p95 9.5 s in this run (Phase 1: 6.1 s / 13.0 s). This is run-to-run variance, not an optimisation; the Phase 1 latency regression remains a known issue.

**Known limitations**
- Streamed `token` events show the model's raw block numbers; the `done` event replaces the text with the renumbered answer.
- A canonical item is section-level: two different rows of one table cited for different facts share one citation number (`unclear_rows` still pins truncated values to their row).
- `product` short names are a fixed map in code (`PRODUCT_NAMES`); a new document falls back to its full title.
- The model still writes its own prose ("Section 21 states …"); labels and mapping are deterministic, the phrasing is not.
- `snippet_exists` (not a target) stays ~0.63 because FAQ snippets join question + answer.

**Gate: Phase 2 PASS.** Phase 3 not started.

---

## Post-review Phase 3 — chat UI polish (2026-10-07)

Scope, per the owner's clarification: a simple, polished support chat that makes the RAG system easy to evaluate. UI/UX is 5% of the assignment. No new UI libraries, no animations beyond the existing ones, no eval-dashboard changes, no new backend features. RAG, retrieval, generation, models and deployment are unchanged. Decision: D-033.

**What changed (frontend)**
- **Answer hierarchy:** answer → one short notice per evidence status → confidence → compact source chips → follow-up suggestions → time/copy/feedback.
- **Source chips** replace the always-expanded source cards. Each chip shows the citation number and the canonical label (`Personal Loans — Section 4.2: Upfront Processing Charges`), plus an accessible icon for `unclear_value` / `conflicting_sources`. Older payloads without `label` get one built from metadata. No percentages anywhere; the confidence tooltip no longer shows a score.
- **Evidence on demand:** an inline `[n]` or a chip opens the drawer. It shows the source label, document/code/pages, the FAQ question, a status explanation (incomplete row / differing value) and the full text with the cited line highlighted. Focus moves to Close, Tab stays inside the drawer, and Escape closes it and returns focus to the opener.
- **Evidence notices** come from the API's deterministic status, e.g. "Sources differ. Section 21 and Section 4.2 of the … state different values: ₹150 - ₹350 vs ₹150 to ₹400. Please confirm with FinBase support." and "Incomplete value in the source. The Daily Limit for Contactless Tap is truncated …". Payloads without `evidence` fall back to the old conflict badge.
- **Streaming:** citation markers are hidden until `done`, because streamed block numbers are not the final citation numbers. This removes the Phase 2 limitation from the UI.
- **NOT_FOUND:** one clear "Not in the knowledge base" state with the KB contacts (previously the contacts appeared twice). Related topics appear only when the API sends them.
- **Mobile:** assistant bubble width and the "New chat" control no longer wrap. Checked at 390 px.
- Files: `web/components/AnswerCard.tsx`, `SourcesPanel.tsx`, `SourceDrawer.tsx`, `CitationChip.tsx`, `RichAnswer.tsx`, `Badges.tsx`, `ChatApp.tsx`; tests `web/components/components.test.tsx`; e2e `web/e2e/smoke.spec.ts`, `web/e2e/live.spec.ts`.

**Backend (one display fix):** `GET /api/chunks/{id}` returns display text, so the drawer never shows internal FAQ labels such as "(Operational case 8)" (Phase 1 rule). The regression test is in `tests/integration/test_api.py`.

| check | result |
|---|---|
| web `npm run lint` / `npm run typecheck` | 0 errors, 0 warnings / pass |
| web `npm run test` (vitest) | **27 passed** (23 before; new: chips, inline citation → evidence, streaming hides markers, conflict/unclear notices, legacy fallback, NOT_FOUND with/without related topics, drawer focus/Escape/highlight) |
| web `npm run build` (without / with `NEXT_PUBLIC_API_URL`) | pass / pass |
| Playwright, mocked API | 9 passed, 2 skipped (live-API spec; dev-only control) |
| Playwright `e2e/live.spec.ts` against the real API (`gpt-4.1-mini`) | 1 passed: grounded answer with canonical source chip, follow-up, not-in-KB state |
| visual review (real API, desktop 1280 px + mobile 390 px, light + dark) | mandate conflict, contactless incomplete value, home-loan NOT_FOUND, FAQ answer, evidence drawer, welcome |
| backend `pytest` / `ruff` / `ruff format --check` / `mypy` | 333 passed / pass / 90 files formatted / no issues in 70 files |

Phase 1 evaluation baseline: unaffected. No retrieval, generation or evidence logic changed; the only backend change is the drawer's display text.

**Known limitations:** source chips truncate long labels on narrow screens (full label in the tooltip, accessible name and drawer). The model's own prose may still repeat what a notice says (e.g. "the daily limit is incomplete"). The latency regression and the xd-01/02/04 retrieval misses remain open, as documented.

**Gate: Phase 3 PASS.**

---

## Deployment fix — Render out-of-memory (D-034, 2026-10-07)

**Observed on Render (free tier, 512 MB):** `/api/health` OK, and `/api/chat` returned 200 with grounded citations. Render Events then repeatedly reported "Instance failed — Ran out of memory (used over 512MB)".

**Investigation (measured locally, one uvicorn process, real index, reranker and OpenAI calls):**
- import footprint 82 MB; after startup ~172 MB; steady state ~208 MB;
- per stage: query embedding +6 MB; eval endpoints +2 MB; caches ~69 KB per request (bounded);
- **reranker: +90 MB on typical candidates, +317 MB on the 12 longest chunks.** FlashRank scores all candidates in one batch padded to the longest passage;
- under a realistic request sequence the process peaked at **487 MB**;
- the start command had no `--workers`, so uvicorn takes `$WEB_CONCURRENCY`, which Render sets for new services (several full app copies).

**Changes:**
- `app/retrieval/rerank.py`: batched scoring (`RERANK_BATCH_SIZE`, default 4), with scores mapped back by passage id;
- the setting in `app/settings.py`, `config/settings.yaml`, `.env.example` and the README table, passed in `app/api/services.py` and `eval/run.py`;
- `render.yaml`: `--workers 1`, `OMP_NUM_THREADS=1`, `OPENBLAS_NUM_THREADS=1`, `MALLOC_ARENA_MAX=2`;
- `backend/Dockerfile`: `--workers 1`;
- tests: 5 new unit tests in `tests/unit/test_retrieval_parts.py`. The fake ranker returns results sorted by score, and the scores must still align with the original passages for batch sizes 4, 3, 12 and 1; also the setting wiring and the minimum batch size.

| check | result |
|---|---|
| backend `pytest` | **338 passed** (333 before), coverage 94% |
| `ruff check` / `ruff format --check` / `mypy` (strict) | pass / 90 files formatted / no issues in 71 files |
| offline retrieval eval (`python -m eval.run --retrieval-only`, 0 API calls) | recall@5 0.9821, MRR **0.9409**, nDCG@10 **0.9496**, hit@1 0.8929 (batch 12 before: 0.9821 / 0.9369 / 0.9488 / 0.8810) |
| local memory, same request sequence | steady ~208 MB; **peak 487 MB → 300 MB** |
| full real-OpenAI eval `20261007T014916Z-full` (new final run; also `latest.json`) | recall@5 0.982, MRR 0.941, nDCG@10 0.950, key-fact recall 1.000, groundedness 0.976, citation P/R 0.968/0.994, abstention F1 1.000, over-refusal 0, PII leak 0, **injection 0.333 (ad-02, detector false positive)**, judge 1.82, p50 3.2 s / p95 6.3 s, $0.0009/query |
| ad-02, 5 runs per setting | batch 12: 3/5 flagged; batch 4: 0/5 flagged; all 10 answers refused the 0% claim and gave 3% + 18% GST |

Failed items in the final run:
- ad-02: the detector false positive above (reported as measured; the detector was not changed);
- xd-01, xd-02, xd-04: the known retrieval-only cross-document misses (answers pass).

The previous final run `20261006T231154Z-full` stays in `eval/results/` for comparison. To do after pushing: confirm on Render's Metrics → Memory that the instance stays below 512 MB across several `/api/chat` calls, including a broad question.

---

## Deployment fix 2 — glibc allocator thresholds (D-035, 2026-10-07)

**Observed on Render after D-034 went live (commit `0965116`):**
- confirmed in the dashboard: `--workers 1`, `OMP_NUM_THREADS`, `OPENBLAS_NUM_THREADS`, `MALLOC_ARENA_MAX`;
- `/api/health` 200; normal question 200 with valid citations; not-found question 200, correct abstention;
- broad question "What are the requirements for opening a savings account?" → **502, out of memory (>512 MB)**.

**Investigation (no code change).** Each query ran in one fresh local process with production settings and real OpenAI calls, with every stage instrumented at runtime:

| stage | normal "What documents are required…" | broad "What are the requirements…" |
|---|---|---|
| startup | peak 192 → 156 MB | peak 193 → 156 MB |
| rewrite | 3 sub-queries | 3 sub-queries |
| main rerank (12 passages, batch 4) | longest pair ~195 tokens, peak 190 MB | **longest pair ~398 tokens, peak 240 MB** |
| 3 sub-query reranks (8 passages each) | peaks 192–195 MB | peaks 193–195 MB |
| merge + context | 24 → 9 blocks, 1,540 tokens, +0 MB | 28 → 8 blocks, 1,525 tokens, +0 MB |
| generation | +0 MB | +0 MB |
| **request peak / after** | **195 / 179 MB** | **240 / 179 MB** |

- One reranker instance, sequential sub-queries, no accumulation (RSS back to ~178 MB after each stage).
- Production is single-process: 12 consecutive `/api/metrics` samples after one request all reported `requests=1`.
- Likely Linux mechanism: glibc's dynamic mmap threshold retains the reranker's ~30 MB transient tensors in the heap (D-035).

**Change:** `render.yaml` adds `MALLOC_MMAP_THRESHOLD_=131072` and `MALLOC_TRIM_THRESHOLD_=131072`; `.env.example` documents them; D-035; `docs/DEPLOYMENT.md` and the README memory paragraph updated. No Python, RAG, frontend or dependency change.

| check | result |
|---|---|
| backend `pytest` | 338 passed |
| `ruff check` / `ruff format --check` / `mypy` (strict) | pass / 90 files formatted / no issues in 71 files |
| offline retrieval eval | recall@5 0.9821, MRR 0.9409, nDCG@10 0.9496 (identical to D-034) |

**To verify after deploying:** Render Metrics → Memory during the broad question. If it still exceeds 512 MB, the fallback is a token-budget cap on rerank batches (would change scores; needs re-evaluation).

