"""Answer orchestration (PROMPT.md §4, §7): PII redaction -> injection flags -> rewrite -> retrieval ->
abstain gate -> LLM (streamed or not) -> deterministic post-processing:

  * NOT_FOUND sentinel -> standard not-found message (KB contacts only) + related sources that are
    genuinely about the question (every content term present), never merely high-scoring ones
  * internal wording ("CONTEXT", "block [2]", a stray NOT_FOUND ...) never reaches the customer
  * citation validation (invalid markers dropped; uncited answer -> one stricter retry, then abstention:
    an answer without valid citations is never shipped as grounded)
  * structural sources only from cited blocks (never from section numbers in the text)
  * figure verification; a figure that "completes" a truncated source value replaces the answer with an
    "unclear in the source" message (never ship a guessed number)
  * evidence status decided in code (app/generation/evidence.py): `unclear_value` per row (confidence Low,
    explicit "cannot be safely determined" note) and `conflicting_sources` (both values with citations,
    confidence at most Medium, same-document wording)
  * system-prompt leak guard, HTML stripping, confidence label, `Answer:` / `Source:` formatting
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

import structlog

from app.cache.ttl_lru import TTLLRUCache
from app.generation.canonical import build_evidence, related_items
from app.generation.citations import (
    CitationReport,
    source_line,
    validate,
)
from app.generation.confidence import AnswerConfidence, combine
from app.generation.evidence import (
    CONFLICTING_SOURCES,
    UNCLEAR_VALUE,
    ConflictEvidence,
    out_of_scope_amounts,
    relevant_conflicts,
    relevant_unclear,
    states_all_values,
    words,
)
from app.generation.prompts import NOT_FOUND, Prompts, build_messages, context_notes
from app.generation.rewrite import Rewrite, corpus_vocabulary, rewrite_query
from app.generation.verifier import Verification, canonical_pool, extract_figures, verify
from app.ingest.models import Chunk
from app.observability.costs import chat_cost, embedding_cost
from app.providers.base import ChatMessage, ChatProvider, ProviderError, Usage
from app.retrieval.context import Candidate
from app.retrieval.gate import GateConfig
from app.retrieval.gate import label as confidence_label
from app.retrieval.pipeline import RetrievalResult, Retriever
from app.safety import injection, intent
from app.safety.pii import Redaction, mentions_sensitive_terms, redact
from app.settings import Settings
from app.text.tokenize import count_tokens, tokenize

log = structlog.get_logger(__name__)

STRICT_REMINDER = (
    "\n\nREMINDER: Every factual sentence MUST end with the supporting block number(s) like [1]. "
    "If the CONTEXT does not contain the answer, reply exactly NOT_FOUND."
)
_CANNOT_DETERMINE = re.compile(
    r"cannot (?:be )?(?:safely )?(?:determine|confirm)|can't (?:safely )?(?:determine|confirm)", re.I
)
INCOMPLETE_VALUE = "an incomplete value (the exact amount cannot be safely determined from the source)"


def cap_confidence(confidence: AnswerConfidence, max_score: float, gate: GateConfig) -> AnswerConfidence:
    """Lower the confidence score to `max_score` (label recomputed); never raises it."""
    if confidence.score <= max_score:
        return confidence
    score = round(max_score, 4)
    return replace(confidence, score=score, label=confidence_label(score, gate))


#: internal pipeline wording that must never reach the customer -> neutral replacement
_INTERNAL = [
    # a stand-alone / trailing sentinel is dropped; inside a sentence it MEANS "not available" (never just
    # deleted: "rates for 2027 are NOT_FOUND in ..." must not become "rates for 2027 are in ...")
    (re.compile(r"(?:(?<=[.!?\]])|^)\s*NOT_FOUND\b\.?(?=\s*$)", re.M), ""),
    (re.compile(r"\bNOT_FOUND\b"), "not available"),
    (re.compile(r"\b(?:the |my )?system prompt\b", re.I), "my instructions"),
    (
        re.compile(
            r"\b(?:in|from|within) the (?:provided |given |retrieved |above )?context(?: blocks?)?(?! of)",
            re.I,
        ),
        "in FinBase's documents",
    ),
    (
        re.compile(r"\bthe (?:provided |given |retrieved |above )context(?: blocks?)?", re.I),
        "FinBase's documents",
    ),
    (re.compile(r"\b(?:the )?CONTEXT\b"), "FinBase's documents"),
    (re.compile(r"\bretrieved (?:context|passages|documents|blocks)\b", re.I), "FinBase's documents"),
    (re.compile(r"\b(?:context )?blocks? (?=\[\d)", re.I), ""),
    (re.compile(r"\b(?:the )?NOTES\b"), "FinBase's documents"),
]
_SAME_DOC = [
    (
        re.compile(r"\b(?:the )?(?:two )?documents (differ|disagree|conflict)\b", re.I),
        r"these sections of the same document \1",
    ),
    (re.compile(r"\b(?:two |the )?different documents\b", re.I), "different sections of the same document"),
    (re.compile(r"\banother document\b", re.I), "another section of the same document"),
]
_QUERY_FILLER = frozenset(["finbase", "tell", "know", "please", "want", "need", "get", "give", "explain"])


def scrub_internal(text: str) -> str:
    """Remove pipeline vocabulary (CONTEXT, blocks, NOTES, NOT_FOUND, system prompt) from a reply."""
    for pattern, replacement in _INTERNAL:
        text = pattern.sub(replacement, text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def same_document_wording(text: str) -> str:
    """A conflict between two sections of ONE manual is not 'different documents'."""
    for pattern, replacement in _SAME_DOC:
        text = pattern.sub(replacement, text)
    return text


def content_terms(text: str) -> set[str]:
    """Specific query terms (stop words and filler removed) used to judge related topics."""
    return {t for t in tokenize(text) if t.isalpha() and len(t) >= 3 and t not in _QUERY_FILLER}


def covers(terms: set[str], chunk: Chunk) -> bool:
    """Every query term occurs in the chunk (exact token or shared 5-letter stem)."""
    tokens = set(tokenize(chunk.embed_text))
    return all(t in tokens or any(len(t) >= 5 and x[:5] == t[:5] for x in tokens) for t in terms)


LEAK_REPLY = "I can't share my internal instructions, but I'm happy to help with questions about FinBase products and policies."


@dataclass
class Event:
    """One SSE event: meta | token | sources | verification | done | error."""

    type: str
    data: Any


@dataclass
class Prepared:
    """State shared by streamed and non-streamed answering."""

    request_id: str
    question: str
    redaction: Redaction
    injection_hits: list[str]
    history: list[ChatMessage]
    rewrite: Rewrite
    retrieval: RetrievalResult
    latency: dict[str, float] = field(default_factory=dict)
    started: float = 0.0


def sanitize(text: str) -> str:
    """No raw HTML reaches the UI: tags are stripped (React renders the rest as text)."""
    return re.sub(r"</?[A-Za-z][^>]{0,200}>", "", text).strip()


def cross_document_figures(answer: str, blocks: Sequence[Candidate]) -> list[str]:
    """Attribution notes for figures that a sentence cites from several documents but that only ONE of them
    states (e.g. a personal-loan bounce fee presented next to a Payments SOP citation)."""
    notes: list[str] = []
    seen: set[str] = set()
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", answer):
        numbers = {int(n) for n in re.findall(r"\[(\d{1,2})\]", sentence) if 1 <= int(n) <= len(blocks)}
        docs = {blocks[n - 1].chunk.doc_id: blocks[n - 1].chunk.doc_title for n in numbers}
        if len(docs) < 2:
            continue
        for fig in extract_figures(sentence):
            if fig.kind != "amount" or fig.canonical in seen:
                continue
            owners = {
                blocks[n - 1].chunk.doc_id
                for n in numbers
                if fig.canonical in canonical_pool([blocks[n - 1].chunk.text])
            }
            if owners and owners != set(docs):
                seen.add(fig.canonical)
                stated = ", ".join(docs[d] for d in sorted(owners))
                absent = ", ".join(docs[d] for d in sorted(set(docs) - owners))
                notes.append(
                    f"Note: {fig.text} is stated in the {stated}, not in the {absent}; it may not apply to that product."
                )
    return notes


def cap_history(history: Sequence[ChatMessage], max_messages: int, max_chars: int) -> list[ChatMessage]:
    """Last N messages, PII-redacted, total length capped (oldest dropped first)."""
    recent = [
        ChatMessage(m.role, redact(m.content).text[:max_chars])
        for m in list(history)[-max_messages:]
        if m.content.strip()
    ]
    while recent and sum(len(m.content) for m in recent) > max_chars:
        recent.pop(0)
    return recent


class AnswerService:
    """Stateless per request; caches are thread-safe."""

    def __init__(
        self,
        settings: Settings,
        retriever: Retriever,
        chat: ChatProvider,
        prompts: Prompts,
        gate: GateConfig,
        pricing: dict[str, Any],
    ) -> None:
        self.settings = settings
        self.retriever = retriever
        self.chat = chat
        self.prompts = prompts
        self.gate = gate
        self.pricing = pricing
        self.answers: TTLLRUCache[dict[str, Any]] = TTLLRUCache(
            settings.cache_max_items, settings.cache_ttl_s
        )
        self._vocabulary: frozenset[str] | None = None

    @property
    def vocabulary(self) -> frozenset[str]:
        """Words of the knowledge base (decides whether a question is confidently plain English)."""
        if self._vocabulary is None:
            self._vocabulary = corpus_vocabulary(c.embed_text for c in self.retriever.store.chunks)
        return self._vocabulary

    # ------------------------------------------------------------------ messages
    @property
    def not_found_message(self) -> str:
        """Standard not-found reply (contacts are the ones published in the KB)."""
        s = self.settings
        return f"I couldn't find this in FinBase's documents. Please contact FinBase support at {s.support_email} or the 24/7 helpline {s.support_helpline}."

    @property
    def unavailable_message(self) -> str:
        """Reply when the LLM provider is down."""
        return "The assistant is temporarily unavailable. Here are the most relevant FinBase documents for your question; please try again shortly."

    def greeting(self, request_id: str | None = None) -> dict[str, Any]:
        """Canned reply for a bare greeting/closing/thanks message (app.safety.intent.is_greeting):
        no PII/injection scan, rewrite, retrieval or LLM call — answered before `prepare()` runs."""
        return {
            "request_id": request_id or uuid.uuid4().hex,
            "rewritten_query": None,
            "language": "en",
            "notices": {"pii": False, "injection": False},
            "conflicts": [],
            "meta": {},
            "evidence": {"statuses": [], "items": [], "claims": [], "unclear_values": [], "conflicts": []},
            "cached": False,
            "degraded": False,
            "answer": intent.GREETING_REPLY,
            "formatted": f"Answer: {intent.GREETING_REPLY}",
            "answerable": True,
            "abstain_reason": None,
            "sources": [],
            "related_sources": [],
            "confidence": {
                "score": 1.0,
                "label": "High",
                "retrieval": 1.0,
                "citation_coverage": 0.0,
                "verified_rate": 1.0,
            },
            "verification": {
                "citations_valid": [],
                "invalid_markers": [],
                "citation_coverage": 0.0,
                "figures": [],
                "unverified_figures": [],
                "repaired_truncations": [],
                "warnings": [],
            },
            "usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "cost_usd": 0.0,
                "latency_ms": {"rewrite": 0.0, "retrieve": 0.0, "rerank": 0.0, "generate": 0.0, "total": 0.0},
                "model": "none",
            },
        }

    # ------------------------------------------------------------------ preparation
    async def prepare(
        self, message: str, history: Sequence[ChatMessage], request_id: str | None = None
    ) -> Prepared:
        """Redact, flag, rewrite and retrieve."""
        started = time.perf_counter()
        redaction = redact(message.strip())
        capped = cap_history(history, self.settings.history_max_messages, self.settings.history_max_chars)
        hits = injection.detect(redaction.text)
        t = time.perf_counter()
        rewritten = await rewrite_query(
            self.chat,
            self.prompts.rewrite_system,
            redaction.text,
            capped,
            model=self.settings.rewrite_model,
            timeout_s=self.settings.rewrite_timeout_s,
            vocabulary=self.vocabulary,
            max_subqueries=0 if hits else self.settings.max_subqueries,  # no fan-out for injection attempts
        )
        rewrite_ms = (time.perf_counter() - t) * 1000
        retrieval = await self.retriever.retrieve_many(rewritten.query, rewritten.subqueries)
        latency = {
            "rewrite": round(rewrite_ms, 1),
            "retrieve": round(retrieval.latency_ms.get("retrieve_total", 0.0), 1),
            "rerank": round(retrieval.latency_ms.get("rerank", 0.0), 1),
        }
        if hits:
            log.info("prompt_injection_flagged", request_id=request_id, patterns=len(hits))
        return Prepared(
            request_id or uuid.uuid4().hex,
            redaction.text,
            redaction,
            hits,
            capped,
            rewritten,
            retrieval,
            latency,
            started,
        )

    def _meta(self, p: Prepared) -> dict[str, Any]:
        return {
            "request_id": p.request_id,
            "rewritten_query": self._rewritten(p),
            "language": p.rewrite.language,
            "retrieval_confidence": p.retrieval.confidence.as_dict(),
            "notices": self._notices(p),
            "retrieval": p.retrieval.debug(),
        }

    @staticmethod
    def _rewritten(p: Prepared) -> str | None:
        """The interpreted query, shown only when the rewrite actually changed the question."""
        if not p.rewrite.used_llm or p.rewrite.query.strip().lower() == p.question.strip().lower():
            return None
        return p.rewrite.query

    def _notices(self, p: Prepared) -> dict[str, bool]:
        return {
            "pii": p.redaction.had_pii or mentions_sensitive_terms(p.question),
            "injection": bool(p.injection_hits),
        }

    def _from_cache(self, hit: dict[str, Any], p: Prepared) -> dict[str, Any]:
        """A cached answer costs nothing and has its own latency (no LLM / rewrite tokens were used now)."""
        latency = {**p.latency, "generate": 0.0, "total": round((time.perf_counter() - p.started) * 1000, 1)}
        usage = {
            **hit["usage"],
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": 0.0,
            "latency_ms": latency,
        }
        return {**hit, "request_id": p.request_id, "cached": True, "usage": usage}

    def notes(self, p: Prepared) -> list[str]:
        """User-turn NOTES for this request (conflicts, missing sections, eligibility, attribution)."""
        return context_notes(
            p.retrieval.blocks, p.retrieval.conflicts, p.retrieval.missing_sections, p.question
        )

    def _cache_key(self, p: Prepared) -> str:
        blocks = ",".join(b.chunk.chunk_id for b in p.retrieval.blocks)
        hist = hashlib.sha256(json.dumps([[m.role, m.content] for m in p.history]).encode()).hexdigest()
        raw = f"{' '.join(p.question.lower().split())}|{p.rewrite.query}|{blocks}|{self.chat.model}|{self.prompts.version}|{hist}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    # ------------------------------------------------------------------ result builders
    def _related(self, p: Prepared, *, relevant_only: bool = True, limit: int = 3) -> list[dict[str, Any]]:
        """Related topics shown with an abstention: only chunks that contain every content term of the
        (interpreted) question — a high retrieval score alone is not relevance — and never another product's
        evidence when the question names a product. None for injection attempts."""
        blocks: Sequence[Candidate] = p.retrieval.blocks
        if relevant_only:
            terms = content_terms(p.rewrite.query)
            if p.injection_hits or not terms:
                return []
            blocks = [b for b in blocks if covers(terms, b.chunk)]
        return related_items(blocks, f"{p.question} {p.rewrite.query}", limit)

    def _usage(self, p: Prepared, answer_usage: Usage, generate_ms: float) -> dict[str, Any]:
        model = self.chat.model
        cost = chat_cost(self.pricing, model, answer_usage.input_tokens, answer_usage.output_tokens)
        cost += chat_cost(
            self.pricing,
            self.settings.rewrite_model,
            p.rewrite.usage.input_tokens,
            p.rewrite.usage.output_tokens,
        )
        cost += (
            embedding_cost(self.pricing, self.settings.embed_model, count_tokens(p.rewrite.query))
            if not p.retrieval.cached
            else 0.0
        )
        latency = {
            **p.latency,
            "generate": round(generate_ms, 1),
            "total": round((time.perf_counter() - p.started) * 1000, 1),
        }
        return {
            "input_tokens": answer_usage.input_tokens + p.rewrite.usage.input_tokens,
            "output_tokens": answer_usage.output_tokens + p.rewrite.usage.output_tokens,
            "cost_usd": round(cost, 6),
            "latency_ms": latency,
            "model": model,
        }

    def _base(self, p: Prepared) -> dict[str, Any]:
        return {
            "request_id": p.request_id,
            "rewritten_query": self._rewritten(p),
            "language": p.rewrite.language,
            "notices": self._notices(p),
            "conflicts": [
                {"doc_id": g.doc_id, "category": g.category, "members": g.members}
                for g in p.retrieval.conflicts
            ],
            "meta": p.retrieval.debug(),
            "evidence": {"statuses": [], "items": [], "claims": [], "unclear_values": [], "conflicts": []},
            "cached": False,
            "degraded": False,
        }

    def not_found(
        self,
        p: Prepared,
        reason: str,
        usage: Usage | None = None,
        generate_ms: float = 0.0,
        *,
        relevant_only: bool = True,
    ) -> dict[str, Any]:
        """Abstention result (no LLM call when `reason` is the retrieval gate)."""
        retrieval_conf = p.retrieval.confidence.score
        return {
            **self._base(p),
            "answer": self.not_found_message,
            "formatted": f"Answer: {self.not_found_message}",
            "answerable": False,
            "abstain_reason": reason,
            "sources": [],
            "related_sources": self._related(p, relevant_only=relevant_only),
            "confidence": {
                "score": round(retrieval_conf, 4),
                "label": "Low",
                "retrieval": round(retrieval_conf, 4),
                "citation_coverage": 0.0,
                "verified_rate": 1.0,
            },
            "verification": {
                "citations_valid": [],
                "invalid_markers": [],
                "citation_coverage": 0.0,
                "figures": [],
                "unverified_figures": [],
                "repaired_truncations": [],
                "warnings": [],
            },
            "usage": self._usage(p, usage or Usage(), generate_ms),
        }

    def degraded(self, p: Prepared, error: str) -> dict[str, Any]:
        """LLM unavailable: retrieved sources + friendly message."""
        result = self.not_found(p, "llm_unavailable", relevant_only=False)
        result.update(
            {
                "answer": self.unavailable_message,
                "formatted": f"Answer: {self.unavailable_message}",
                "degraded": True,
                "error": error,
            }
        )
        result["verification"]["warnings"] = ["llm_unavailable"]
        return result

    def finalize(
        self, p: Prepared, raw: str, usage: Usage, generate_ms: float, *, final: bool = True
    ) -> dict[str, Any]:
        """Deterministic post-processing of one LLM output.

        `final=False` (first attempt of a non-streamed answer) returns an uncited answer flagged
        `ungrounded_no_citations` so the caller can retry once; a final uncited answer becomes an abstention.
        """
        text = sanitize(raw)
        if not text or text.upper().startswith(NOT_FOUND):
            return self.not_found(p, "llm_not_found", usage, generate_ms)
        if injection.leaks_system_prompt(text, self.prompts.answer_system):
            result = self.not_found(p, "system_prompt_leak_blocked", usage, generate_ms)
            result.update({"answer": LEAK_REPLY, "formatted": f"Answer: {LEAK_REPLY}"})
            result["verification"]["warnings"] = ["system_prompt_leak_blocked"]
            return result
        text = scrub_internal(text)
        blocks = p.retrieval.blocks
        report: CitationReport = validate(text, len(blocks))
        invalid_markers = report.invalid
        if not report.valid and final:
            result = self.not_found(p, "ungrounded_no_citations", usage, generate_ms)
            result["verification"]["warnings"] = ["ungrounded_answer_blocked"]
            return result
        cited = [blocks[n - 1].chunk for n in report.valid]
        verification: Verification = verify(
            report.text, cited or [b.chunk for b in blocks], p.question, trust_question=not p.injection_hits
        )
        warnings: list[str] = []
        answer = report.text
        if verification.repaired_truncations:
            suspect = next(
                (n for n in report.valid if blocks[n - 1].chunk.quality_flag == "suspect_value"), None
            )
            suspect = suspect or next(
                (i for i, b in enumerate(blocks, start=1) if b.chunk.quality_flag == "suspect_value"), 1
            )
            rows = relevant_unclear(
                [blocks[suspect - 1].chunk], words(p.question) | words(p.rewrite.query), report.text
            )
            item = rows[0].describe() if rows else "the exact amount for this item"
            answer = (
                f"In FinBase's source document, {item} appears incomplete or unclear (the figure is truncated) "
                f"[{suspect}], so its exact value cannot be safely determined. Please contact FinBase support at {self.settings.support_email} "
                f"or {self.settings.support_helpline} for the exact value."
            )
            report = validate(answer, len(blocks))
            verification = verify(answer, [blocks[suspect - 1].chunk], p.question, trust_question=False)
            warnings.append("repaired_truncated_value_blocked")
        quoted = [f.text for f in verification.figures if f.canonical.startswith("malformed:")]
        if quoted:
            # Never present a truncated amount (e.g. "₹5,00,0") as a usable figure: replace it verbatim.
            # First mention gets the explanation unless the answer already gives it; later mentions a short form.
            pattern = re.compile("|".join(re.escape(v) for v in dict.fromkeys(quoted)) + r"(?!\d|,\d)")
            first = "an incomplete value" if _CANNOT_DETERMINE.search(answer) else INCOMPLETE_VALUE
            answer = pattern.sub("an incomplete value", pattern.sub(first, answer, count=1))
            warnings.append("garbled_value_flagged")

        # ---- evidence status, decided in code -------------------------------------------------------
        chunks = [b.chunk for b in blocks]
        question_words = words(p.question) | words(p.rewrite.query)
        unclear = relevant_unclear(chunks, question_words, answer, quoted)
        if (
            unclear
            and "repaired_truncated_value_blocked" not in warnings
            and not _CANNOT_DETERMINE.search(answer)
        ):
            value = unclear[0]
            n = next(i for i, c in enumerate(chunks, start=1) if c.chunk_id == value.chunk_id)
            answer += (
                f"\n\nNote: {value.describe()} appears incomplete in FinBase's source document [{n}], so the exact "
                f"value cannot be safely determined. Please confirm it with FinBase support at {self.settings.support_email}."
            )
        report = validate(answer, len(blocks))
        conflicts = relevant_conflicts(
            p.retrieval.conflicts, chunks, question_words, answer, set(report.valid)
        )
        for conflict in conflicts:
            answer = self._present_conflict(conflict, answer, set(report.valid))
            report = validate(answer, len(blocks))
        cited_now = [blocks[n - 1].chunk for n in report.valid] or chunks
        verification = verify(answer, cited_now, p.question, trust_question=not p.injection_hits)

        # Product scope (decided in code): a fee asked for one product, answered only with another product's
        # figure (UPI AutoPay bounce fee <- personal-loan EMI bounce fee), is not an answer.
        cited_chunks = [blocks[n - 1].chunk for n in validate(answer, len(blocks)).valid]
        transferred = out_of_scope_amounts(f"{p.question} {p.rewrite.query}", answer, cited_chunks)
        if transferred:
            log.info("product_scope_mismatch", request_id=p.request_id, figures=len(transferred))
            result = self.not_found(p, "product_scope_mismatch", usage, generate_ms)
            result["related_sources"] = []  # never point at the other product's evidence
            result["verification"]["warnings"] = ["product_scope_mismatch"]
            return result
        attribution = cross_document_figures(answer, blocks)
        if attribution:
            answer += "\n\n" + "\n".join(attribution)
            report = validate(answer, len(blocks))
            warnings.append("cross_document_figure")
        if not report.valid:
            warnings.append("ungrounded_no_citations")
        if verification.unverified:
            warnings.append("unverified_figures")
        if unclear:
            warnings.extend(["source_value_unclear", UNCLEAR_VALUE])
        if conflicts:
            warnings.append(CONFLICTING_SOURCES)
        if p.injection_hits:
            warnings.append("injection_detected")
        if p.redaction.had_pii:
            warnings.append("pii_redacted")
        confidence = combine(
            p.retrieval.confidence.score, report.coverage, verification.rate, bool(report.valid), self.gate
        )
        # Confidence caps: an unclear requested value -> Low; disagreeing sources -> at most Medium.
        if unclear or "repaired_truncated_value_blocked" in warnings:
            confidence = cap_confidence(confidence, self.gate.medium - 0.01, self.gate)
        if conflicts or attribution:
            confidence = cap_confidence(confidence, self.gate.high - 0.01, self.gate)
        # Canonical evidence (Phase 2): de-duplicated logical sources, citation numbers in first-citation order,
        # markers rewritten, per-item status / scope, claim -> evidence mapping.
        evidence = build_evidence(answer, blocks, unclear, conflicts, f"{p.question} {p.rewrite.query}")
        answer = evidence.answer
        sources = evidence.cited
        line = source_line(sources)
        return {
            **self._base(p),
            "answer": answer,
            "formatted": f"Answer: {answer}" + (f"\n{line}" if line else ""),
            "answerable": True,
            "abstain_reason": None,
            "sources": [s.as_dict() for s in sources],
            "related_sources": [],
            "confidence": confidence.__dict__,
            "verification": {
                "citations_valid": [s.n for s in sources],
                "invalid_markers": invalid_markers,
                "citation_coverage": round(report.coverage, 4),
                **verification.as_dict(),
                "warnings": warnings,
            },
            "evidence": evidence.as_dict(),
            "usage": self._usage(p, usage, generate_ms),
        }

    def _present_conflict(self, conflict: ConflictEvidence, answer: str, cited: set[int]) -> str:
        """Make sure every side of a genuine conflict is stated with its citation; same-document wording."""
        if conflict.same_document:
            answer = same_document_wording(answer)
        members = [m for m in conflict.group.members if m in conflict.blocks]
        if states_all_values(conflict, answer, cited) or not all(conflict.values.get(m) for m in members):
            return answer
        sides = " versus ".join(
            f"{conflict.values[m]} ({conflict.sections[i]}) [{conflict.blocks[m]}]"
            for i, m in enumerate(members)
        )
        where = " and ".join(conflict.sections)
        return answer + (
            f"\n\nNote: {where} of the {conflict.doc_title} state different amounts for this item: {sides}. "
            f"Please confirm the applicable amount with FinBase support at {self.settings.support_email}."
        )

    # ------------------------------------------------------------------ non-streamed
    async def answer(
        self, message: str, history: Sequence[ChatMessage] = (), request_id: str | None = None
    ) -> dict[str, Any]:
        """Full answer object (one stricter retry when the model returns an uncited factual answer)."""
        if intent.is_greeting(message):
            return self.greeting(request_id)
        p = await self.prepare(message, history, request_id)
        if p.retrieval.confidence.abstain or not p.retrieval.blocks:
            return self.not_found(p, "low_retrieval_confidence")
        key = self._cache_key(p)
        if (hit := self.answers.get(key)) is not None:
            return self._from_cache(hit, p)
        messages = build_messages(
            p.history,
            p.retrieval.blocks,
            p.question,
            p.rewrite.query,
            self.settings.history_max_messages,
            self.notes(p),
        )
        t = time.perf_counter()
        try:
            completion = await self.chat.complete(messages, system=self.prompts.answer_system)
            result = self.finalize(
                p, completion.text, completion.usage, (time.perf_counter() - t) * 1000, final=False
            )
            if result["answerable"] and "ungrounded_no_citations" in result["verification"]["warnings"]:
                retry = await self.chat.complete(
                    messages, system=self.prompts.answer_system + STRICT_REMINDER
                )
                usage = Usage(
                    completion.usage.input_tokens + retry.usage.input_tokens,
                    completion.usage.output_tokens + retry.usage.output_tokens,
                )
                result = self.finalize(p, retry.text, usage, (time.perf_counter() - t) * 1000)
                result["verification"]["warnings"].append("retried_for_citations")
        except ProviderError as exc:
            log.error("llm_unavailable", request_id=p.request_id, error=str(exc))
            return self.degraded(p, "llm_unavailable")
        if not result.get("degraded"):
            self.answers.set(key, result)
        return result

    # ------------------------------------------------------------------ streamed
    async def stream(
        self, message: str, history: Sequence[ChatMessage] = (), request_id: str | None = None
    ) -> AsyncIterator[Event]:
        """meta -> token* -> sources -> verification -> done (or error + done when the LLM fails).

        Output is buffered until it cannot be the NOT_FOUND sentinel, so the sentinel never reaches the UI.
        The `done` event carries the authoritative, post-processed answer.
        """
        if intent.is_greeting(message):
            result = self.greeting(request_id)
            yield Event(
                "meta",
                {
                    "request_id": result["request_id"],
                    "rewritten_query": None,
                    "language": result["language"],
                    "retrieval_confidence": None,
                    "notices": result["notices"],
                    "retrieval": {},
                },
            )
            async for event in self._emit_final(result, streamed=False):
                yield event
            return
        p = await self.prepare(message, history, request_id)
        yield Event("meta", self._meta(p))
        if p.retrieval.confidence.abstain or not p.retrieval.blocks:
            async for event in self._emit_final(
                self.not_found(p, "low_retrieval_confidence"), streamed=False
            ):
                yield event
            return
        key = self._cache_key(p)
        if (hit := self.answers.get(key)) is not None:
            async for event in self._emit_final(self._from_cache(hit, p), streamed=False):
                yield event
            return
        messages = build_messages(
            p.history,
            p.retrieval.blocks,
            p.question,
            p.rewrite.query,
            self.settings.history_max_messages,
            self.notes(p),
        )
        parts: list[str] = []
        buffer = ""
        emitted = False
        usage = Usage()
        t = time.perf_counter()
        try:
            async for chunk in self.chat.stream(messages, system=self.prompts.answer_system):
                if chunk.done:
                    usage = chunk.usage or usage
                    continue
                parts.append(chunk.delta)
                if emitted:
                    yield Event("token", chunk.delta)
                    continue
                buffer += chunk.delta
                head = buffer.lstrip()
                if NOT_FOUND.startswith(head[: len(NOT_FOUND)]) and (
                    len(head) < len(NOT_FOUND) or head.startswith(NOT_FOUND)
                ):
                    continue  # could still be / is the sentinel: hold back
                emitted = True
                yield Event("token", buffer)
        except ProviderError as exc:
            log.error("llm_unavailable", request_id=p.request_id, error=str(exc))
            yield Event("error", {"code": "llm_unavailable", "message": self.unavailable_message})
            async for event in self._emit_final(self.degraded(p, "llm_unavailable"), streamed=False):
                yield event
            return
        result = self.finalize(p, "".join(parts), usage, (time.perf_counter() - t) * 1000)
        self.answers.set(key, result)
        async for event in self._emit_final(result, streamed=emitted):
            yield event

    async def _emit_final(self, result: dict[str, Any], *, streamed: bool) -> AsyncIterator[Event]:
        if not streamed:
            yield Event("token", result["answer"])
        yield Event("sources", {"sources": result["sources"], "related_sources": result["related_sources"]})
        yield Event("verification", {**result["verification"], "confidence": result["confidence"]})
        yield Event("done", result)
