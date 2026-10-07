# API reference

Base URL: local `http://localhost:8000`, production `https://<render-service>.onrender.com`. Interactive docs are at `/docs` (Swagger) and `/openapi.json`. All endpoints are under `/api`. Every response carries an `X-Request-ID` header, which you can also send yourself.

## `POST /api/chat`

Request body:

| field | type | notes |
|---|---|---|
| `message` | string | required, 1–2,000 chars; blank/whitespace → 422 |
| `history` | `[{role: "user"\|"assistant", content}]` | optional; last 6 messages are used (the client sends it every turn so the API survives restarts) |
| `session_id` | string | optional; `[A-Za-z0-9_-]`, enables a 1-hour server-side history fallback |
| `stream` | bool | `true` → `text/event-stream` |

Non-streamed response (abridged):

```json
{
  "request_id": "5f0c…",
  "answer": "Closing before 24 months costs 3% of the outstanding principal [1]…",
  "formatted": "Answer: …\nSource: FinBase Personal Loans Master Policy & Operational Manual — Section 6.2 (p. 3)",
  "answerable": true,
  "sources": [{"n": 1, "evidence_id": "personal_loans:section:6.2",
               "label": "Personal Loans — Section 6.2: Foreclosure Charges & Rules",
               "citation": "FinBase Personal Loans Master Policy & Operational Manual — Section 6.2 (p. 3)",
               "doc_id": "personal_loans", "doc_title": "…", "doc_code": "FB-POL-PL-2026-V4", "product": "Personal Loans",
               "section_id": "6.2", "section_title": "Foreclosure Charges & Rules", "page_start": 3, "page_end": 3,
               "source_type": "section", "chunk_type": "policy", "chunk_id": "6a26b186cbb07951", "chunk_ids": ["6a26b186cbb07951"],
               "snippet": "…", "status": "normal", "cited": true, "role": "primary", "scope": "in_scope",
               "faq_id": null, "faq_question": null, "quality_flag": "ok", "unclear_rows": [], "conflict_ids": []}],
  "related_sources": [],
  "confidence": {"score": 0.86, "label": "High", "retrieval": 0.81, "citation_coverage": 1.0, "verified_rate": 1.0},
  "verification": {"citations_valid": [1], "invalid_markers": [], "unverified_figures": [], "warnings": []},
  "evidence": {"statuses": [], "items": ["…same objects as sources, plus uncited status-bearing items…"],
               "claims": [{"text": "Closing before 24 months costs 3% of the outstanding principal.",
                           "citations": [1], "evidence_ids": ["personal_loans:section:6.2"]}],
               "unclear_values": [], "conflicts": []},
  "usage": {"input_tokens": 1450, "output_tokens": 61, "cost_usd": 0.00068,
            "latency_ms": {"rewrite": 0, "retrieve": 410, "rerank": 120, "generate": 1300, "total": 1800}},
  "rewritten_query": null, "language": "en", "notices": {"pii": false, "injection": false},
  "conflicts": [], "cached": false, "degraded": false, "meta": {"candidates": ["…per-stage scores…"]}
}
```

(The numbers above illustrate the shape, not measured values. Measured latency and cost are in `docs/EVAL_REPORT.md`.)

Abstention: `answerable: false`, `abstain_reason` is one of `low_retrieval_confidence`, `llm_not_found`, `system_prompt_leak_blocked` or `llm_unavailable`. The answer is the standard message with KB contacts, and `related_sources` holds the closest sources.

### Streaming (SSE)

Response headers: `Content-Type: text/event-stream`, `Cache-Control: no-cache`, `Connection: keep-alive`, `X-Accel-Buffering: no`. Events, in order:

| event | data |
|---|---|
| `meta` | `{request_id, rewritten_query, language, retrieval_confidence, notices, retrieval}` |
| `token` | JSON string delta (several events) |
| `sources` | `{sources, related_sources}` |
| `verification` | verification object + `confidence` |
| `done` | the full final object (authoritative text, same shape as the non-streamed response) |
| `error` | `{code, message}`, followed by `done` with a degraded result |

`: heartbeat` comment lines are sent every 15 s while idle. Closing the connection cancels the upstream OpenAI stream.

```bash
curl -N -X POST http://localhost:8000/api/chat \
  -H 'Content-Type: application/json' -H 'Accept: text/event-stream' \
  -d '{"message":"What is the daily UPI limit?","stream":true}'
```

```bash
curl -s -X POST http://localhost:8000/api/chat -H 'Content-Type: application/json' \
  -d '{"message":"and after 24 months?","history":[{"role":"user","content":"Foreclosure charge after 18 months?"},{"role":"assistant","content":"3% [1]"}]}'
```

PowerShell:

```powershell
Invoke-RestMethod -Uri http://localhost:8000/api/chat -Method Post -ContentType 'application/json' `
  -Body (@{ message = 'What is the minimum amount due on my credit card?' } | ConvertTo-Json)
```

## Other endpoints

| method & path | purpose |
|---|---|
| `GET /api/health` | liveness + provider, chat/embed model, reranker status, chunk count, gate-calibration flag, index manifest (no secrets) |
| `GET /api/ready` | 200 once the index is loaded |
| `GET /api/docs/list` | documents with their indexed sections and page spans |
| `GET /api/chunks/{chunk_id}` | full chunk (text, breadcrumb, metadata, duplicates) for the sources drawer; 404 if unknown |
| `POST /api/feedback` | `{request_id, rating: "up"\|"down", comment?}` → appended to `logs/feedback.jsonl` (comment PII-redacted) |
| `GET /api/metrics` | latency p50/p95 per stage, request/abstain/error/degraded rates, answer-cache hit rate, per-cache hit rates, tokens, cost |
| `GET /api/eval/latest` | latest evaluation JSON (`eval/results/latest.json`) for the dashboard; 404 if none |
| `GET /api/eval/runs` | summaries of all stored runs |

## Errors

All errors are JSON without stack traces: `422 {"error":"invalid_request","detail":[…]}`, `413 payload_too_large`, `429 rate_limited` (default 20/min per IP), `500 internal_error`. Each includes `request_id`.

### Evidence & citation contract (Phase 2, D-032)

**Evidence item** (`sources[]` = the cited items; `evidence.items[]` = cited items plus uncited items that carry a status):

| field | meaning |
|---|---|
| `evidence_id` | logical source identity, stable across requests: `<doc_id>:section:<section_id>` or `<doc_id>:faq:<Qnnn>` |
| `n` | citation number (1..k, order of first citation in the answer); `null` for an uncited item; `0` on related topics |
| `label` | human-readable reference from metadata, e.g. `Personal Loans — Section 4.2: Upfront Processing Charges`, `Credit Cards — FAQ Q007` |
| `citation` | structural citation with the full document title and page(s) (used in `formatted`'s `Source:` line) |
| `doc_id`, `doc_title`, `doc_code`, `product` | document / product identity (`product` is the short name: Personal Loans, Credit Cards, Savings Account, UPI Payments, FD & Wealth, KYC & Security) |
| `section_id`, `section_title`, `page_start`, `page_end` | location; pages span every chunk of the item |
| `source_type` | `section` \| `table` \| `row` \| `faq` \| `annex` (of the representative chunk) |
| `chunk_id`, `chunk_ids` | representative chunk (for `GET /api/chunks/{id}`) and every chunk merged into this item |
| `snippet` | the line most relevant to the answer; FAQ: question + answer (no internal case labels) |
| `status` | `normal` \| `unclear_value` \| `conflicting_sources` |
| `cited`, `role` | cited by the answer; `primary` / `secondary` (FAQ when a body section is also cited) / `related` |
| `scope` | `in_scope` (a product the question names), `general` (KYC, cross-cutting), `other_product`, `unspecified` (the question names no product) |
| `faq_id`, `faq_question` | canonical FAQ id and question text |
| `unclear_rows` | rows of this item with a truncated value the request is about (`chunk_id`, `line`, `column`, `row`; never the garbled figure) |
| `conflict_ids` | ids of `evidence.conflicts[]` this item belongs to |
| `quality_flag` | legacy: `suspect_value` iff `unclear_rows` is non-empty |

No retrieval score or percentage is part of an evidence item (`relevance` and `scores` were removed). Raw per-stage
retrieval scores remain only in the `meta` debug object.

**Citation numbers.** The model cites the context blocks it was given; after all safety post-processing the
answer's markers are rewritten to the canonical numbers. A marker run that points at one item becomes one marker
(`[2][3]` → `[1]` when blocks 2 and 3 are the same section). `verification.citations_valid` equals the `n` of
`sources`, and every marker in `answer` is one of them. Same answer + same evidence ⇒ same numbering, whatever the
retrieval order. While streaming, `token` events carry the model's raw block numbers; the `done` event carries the
renumbered answer, which replaces the streamed text.

**De-duplication.** One item per logical source: a table row and its table, overlapping chunks of one section, and
an orphan FAQ question with its full entry are merged (the full FAQ entry / the table is the representative chunk).
Different sections are never merged, so both sides of a conflict stay separate items.

**Statuses.** `evidence.statuses` holds zero or more of:
- `unclear_value`: a row the question is about holds a truncated or garbled amount. The item has `status: unclear_value`
  and lists the row in `unclear_rows`; complete rows of the same table do not make an item unclear.
  `evidence.unclear_values[]` adds `evidence_id` and the citation `n`. Confidence is Low.
- `conflicting_sources`: authoritative passages give different values. Every side is its own item with
  `status: conflicting_sources`. `evidence.conflicts[]` gives `conflict_id`, `category`, `scope`
  (`same_document` / `cross_document`), `sections`, `values`, `evidence_ids`, `citations` and a `description`
  ("Section 21 and Section 4.2 of the … state different values."). Confidence is at most Medium.

**Claims.** `evidence.claims[]` maps each cited answer sentence to its citation numbers and evidence ids.

`GET /api/chunks/{id}` returns the chunk's display text (FAQ entries without internal `(… case N)` labels), the same text the model saw.

**Abstentions** (`answerable: false`) carry no evidence items. `related_sources` (same item shape, `n: 0`) appear only
when every content term of the question is covered, never for injection attempts or product-scope mismatches, and
never from another product than the one the question names.

