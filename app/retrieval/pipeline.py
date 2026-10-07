"""Hybrid retrieval pipeline (PROMPT.md §6): preprocess -> dense + BM25 -> RRF -> weights -> rerank ->
context assembly -> confidence/abstain. CPU-bound steps run in a worker thread (never block the loop).

Broad questions (`retrieve_many`): the main query plus up to `max_subqueries` sub-queries each run the same
pipeline; their reranked lists are interleaved (main first, de-duplicated) and assembled together with a
larger budget, so e.g. "requirements for opening a savings account" gets savings onboarding AND the KYC
tiers / documents. The abstain decision stays with the main query."""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from app.cache.ttl_lru import TTLLRUCache
from app.providers.base import EmbeddingProvider, Vectors
from app.retrieval.context import Assembly, AssemblyConfig, Candidate, ConflictGroup, assemble
from app.retrieval.fusion import rrf
from app.retrieval.gate import Confidence, GateConfig, evaluate
from app.retrieval.rerank import Reranker
from app.retrieval.router import route
from app.retrieval.store import IndexStore
from app.settings import Settings
from app.text.numbers import normalize_query_numbers
from app.text.tokenize import expand_abbreviations, tokenize

Mode = Literal["dense", "bm25", "hybrid", "hybrid_rerank"]


@dataclass
class RetrievalResult:
    """Everything downstream needs (blocks) plus debug info for the eval dashboard."""

    query: str
    lexical_query: str
    mode: Mode
    blocks: list[Candidate]
    ranked: list[Candidate]
    confidence: Confidence
    routed_docs: list[str]
    conflicts: list[ConflictGroup] = field(default_factory=list)
    missing_sections: list[dict[str, str]] = field(default_factory=list)
    latency_ms: dict[str, float] = field(default_factory=dict)
    cached: bool = False
    subqueries: list[str] = field(default_factory=list)

    def debug(self) -> dict[str, Any]:
        """Retrieval debug payload (ids + per-stage scores)."""
        return {
            "mode": self.mode,
            "lexical_query": self.lexical_query,
            "routed_docs": self.routed_docs,
            "candidates": [c.debug() for c in self.ranked],
            "selected": [c.chunk.chunk_id for c in self.blocks],
            "conflicts": [
                {"doc_id": g.doc_id, "category": g.category, "members": g.members} for g in self.conflicts
            ],
            "confidence": self.confidence.as_dict(),
            "cached": self.cached,
            "subqueries": self.subqueries,
        }


_GENERIC_TITLE_WORDS = frozenset(
    ["section", "policy", "matrix", "standards", "workflow", "master", "schedule", "operating", "timelines"]
)


def matching_missing_sections(query: str, missing: list[dict[str, str]]) -> list[dict[str, str]]:
    """TOC-listed sections with no body whose title the query is clearly about (>= 50% of the title's
    specific words appear in the query)."""
    q = set(tokenize(query))
    out = []
    for section in missing:
        words = {w for w in tokenize(section["title"]) if w not in _GENERIC_TITLE_WORDS and len(w) > 2}
        if words and len(words & q) / len(words) >= 0.5:
            out.append(section)
    return out


def diversify(ranked: list[Candidate], pool: list[Candidate], docs: list[str], k: int) -> list[Candidate]:
    """Multi-document questions: make sure each routed document's best chunk is inside the top-k."""
    out = list(ranked)
    for doc_id in docs:
        if any(c.chunk.doc_id == doc_id for c in out[:k]):
            continue
        best = next(
            (c for c in [*out, *pool] if c.chunk.doc_id == doc_id and c.chunk.chunk_type != "table_row"), None
        )
        if best is None:
            continue
        if best in out:
            out.remove(best)
        out.insert(max(0, k - 1), best)
    return out


def preprocess(query: str) -> str:
    """Lexical copy of the (already PII-redacted) query: number/currency normalisation + abbreviation expansion."""
    return expand_abbreviations(normalize_query_numbers(query))


class Retriever:
    """Stateless per request; shared caches are thread-safe."""

    def __init__(
        self,
        store: IndexStore,
        embedder: EmbeddingProvider,
        reranker: Reranker,
        settings: Settings,
        gate_config: GateConfig,
        conflicts: list[ConflictGroup] | None = None,
    ) -> None:
        self.store = store
        self.embedder = embedder
        self.reranker = reranker
        self.settings = settings
        self.gate_config = gate_config
        self.conflicts = conflicts or []
        self.query_vectors: TTLLRUCache[Vectors] = TTLLRUCache(settings.cache_max_items, settings.cache_ttl_s)
        self.results: TTLLRUCache[RetrievalResult] = TTLLRUCache(
            settings.cache_max_items, settings.cache_ttl_s
        )

    def _cache_key(self, query: str, mode: str, rerank_top_n: int) -> str:
        normalised = " ".join(query.lower().split())
        raw = f"{normalised}|{self.store.manifest_hash}|{mode}|{rerank_top_n}"
        return hashlib.sha256(raw.encode()).hexdigest()

    async def embed_query(self, query: str) -> Vectors:
        """Query embedding (in-memory TTL cache)."""
        key = f"{self.embedder.name}|{self.embedder.model}|{query}"
        cached = self.query_vectors.get(key)
        if cached is not None:
            return cached
        vector = await self.embedder.embed([query], "query")
        self.query_vectors.set(key, vector)
        return vector

    async def retrieve(
        self,
        query: str,
        mode: Mode = "hybrid_rerank",
        *,
        use_cache: bool = True,
        rerank_top_n: int | None = None,
    ) -> RetrievalResult:
        """Run the full retrieval pipeline for one standalone (English) query."""
        top_n = rerank_top_n or self.settings.rerank_top_n
        key = self._cache_key(query, mode, top_n)
        if use_cache and (hit := self.results.get(key)) is not None:
            return RetrievalResult(**{**hit.__dict__, "cached": True})
        timings: dict[str, float] = {}
        t0 = time.perf_counter()
        lexical = preprocess(query)
        vector = await self.embed_query(query) if mode != "bm25" else None
        timings["embed"] = (time.perf_counter() - t0) * 1000
        result = await asyncio.to_thread(self._search_sync, query, lexical, vector, mode, timings, top_n)
        timings["retrieve_total"] = (time.perf_counter() - t0) * 1000
        result.latency_ms = timings
        if use_cache:
            self.results.set(key, result)
        return result

    async def retrieve_many(
        self, query: str, subqueries: list[str], mode: Mode = "hybrid_rerank"
    ) -> RetrievalResult:
        """Main query + sub-queries (broad questions); a plain `retrieve` when there are none."""
        main = await self.retrieve(query, mode)
        seen = {" ".join(query.lower().split())}
        subs: list[str] = []
        for sub in subqueries:
            norm = " ".join(sub.lower().split())
            if norm and norm not in seen:
                seen.add(norm)
                subs.append(sub.strip())
        subs = subs[: self.settings.max_subqueries]
        if not subs:
            return main
        t0 = time.perf_counter()
        results = [
            await self.retrieve(q, mode, rerank_top_n=self.settings.subquery_rerank_top_n) for q in subs
        ]
        merged = await asyncio.to_thread(self._merge_sync, main, results, subs)
        merged.latency_ms = {
            **main.latency_ms,
            "subqueries": (time.perf_counter() - t0) * 1000,
            "retrieve_total": main.latency_ms.get("retrieve_total", 0.0) + (time.perf_counter() - t0) * 1000,
        }
        return merged

    def _merge_sync(
        self, main: RetrievalResult, results: list[RetrievalResult], subs: list[str]
    ) -> RetrievalResult:
        lists = [main.ranked] + [r.ranked for r in results]
        merged: list[Candidate] = []
        seen: set[str] = set()
        for depth in range(max(len(r) for r in lists)):
            for ranked in lists:  # interleave: every query's best evidence gets in, main query first
                if depth < len(ranked) and ranked[depth].chunk.chunk_id not in seen:
                    seen.add(ranked[depth].chunk.chunk_id)
                    merged.append(ranked[depth])
        routed = sorted({d for q in [main.query, *subs] for d in route(q)})
        s = self.settings
        assembly = self._assemble(merged, merged, routed, s.multi_query_final_k, s.multi_query_token_budget)
        missing = list(
            {
                m["section_id"] + m["doc_title"]: m for r in [main, *results] for m in r.missing_sections
            }.values()
        )
        # The answer is built from every query's evidence, so its retrieval score is the best-supported query's;
        # the abstain decision stays with the main query (sub-queries never rescue an out-of-scope question).
        best = max([main, *results], key=lambda r: r.confidence.score).confidence
        confidence = replace(best, abstain=main.confidence.abstain)
        return RetrievalResult(
            query=main.query,
            lexical_query=main.lexical_query,
            mode=main.mode,
            blocks=assembly.blocks,
            ranked=merged,
            confidence=confidence,
            routed_docs=routed,
            conflicts=assembly.conflicts,
            missing_sections=missing,
            subqueries=subs,
        )

    def _assemble(
        self, ranked: list[Candidate], pool: list[Candidate], routed: list[str], final_k: int, budget: int
    ) -> Assembly:
        config = AssemblyConfig(
            final_k=final_k, max_per_section=self.settings.max_per_section, token_budget=budget
        )
        ranked_ids = {c.chunk.chunk_id for c in ranked}
        return assemble(
            ranked,
            config,
            self.store.chunks,
            self.conflicts,
            cover_docs=routed if len(routed) >= 2 else (),
            coverage_pool=[*ranked, *(c for c in pool if c.chunk.chunk_id not in ranked_ids)],
        )

    def _search_sync(
        self,
        query: str,
        lexical: str,
        vector: Vectors | None,
        mode: Mode,
        timings: dict[str, float],
        rerank_top_n: int,
    ) -> RetrievalResult:
        s = self.settings
        t = time.perf_counter()
        dense = self.store.dense_search(vector, s.dense_k) if vector is not None else []
        lexical_hits = self.store.bm25_search(lexical, s.bm25_k) if mode != "dense" else []
        timings["search"] = (time.perf_counter() - t) * 1000

        rankings = {name: hits for name, hits in (("dense", dense), ("bm25", lexical_hits)) if hits}
        fused = rrf(rankings, k=s.rrf_k)
        routed = route(query)
        candidates: dict[int, Candidate] = {}
        for idx, value in fused.items():
            chunk = self.store.chunks[idx]
            weight = 1.0
            if chunk.boilerplate:
                weight *= s.boilerplate_weight
            if chunk.chunk_type == "faq":
                weight *= s.faq_weight
            if chunk.doc_id in routed:
                weight *= s.router_boost
            candidates[idx] = Candidate(idx=idx, chunk=chunk, rrf=value, weight=weight, final=value * weight)
        for rank, (idx, score) in enumerate(dense, start=1):
            candidates[idx].dense, candidates[idx].dense_rank = score, rank
        for rank, (idx, score) in enumerate(lexical_hits, start=1):
            candidates[idx].bm25, candidates[idx].bm25_rank = score, rank
        ranked = sorted(candidates.values(), key=lambda c: (-c.final, c.chunk.chunk_type == "faq", c.idx))

        if mode == "hybrid_rerank" and ranked:
            t = time.perf_counter()
            pool = ranked[:rerank_top_n]
            scores = self.reranker.score(query, [c.chunk.embed_text for c in pool])
            timings["rerank"] = (time.perf_counter() - t) * 1000
            if scores is not None:
                for cand, value in zip(pool, scores, strict=True):
                    cand.rerank = value
                    cand.final = value * cand.weight
                ranked = (
                    sorted(pool, key=lambda c: (-c.final, c.chunk.chunk_type == "faq", c.idx))
                    + ranked[rerank_top_n:]
                )
        pool = sorted(candidates.values(), key=lambda c: (-c.final, c.chunk.chunk_type == "faq", c.idx))
        ranked = ranked[: max(rerank_top_n, s.final_k)]
        if len(routed) >= 2:
            ranked = diversify(ranked, pool, routed, s.final_k)

        assembly = self._assemble(ranked, pool, routed, s.final_k, s.context_token_budget)
        confidence = evaluate(query, ranked, assembly.blocks, self.gate_config)
        return RetrievalResult(
            query=query,
            lexical_query=lexical,
            mode=mode,
            blocks=assembly.blocks,
            ranked=ranked,
            confidence=confidence,
            routed_docs=sorted(routed),
            conflicts=assembly.conflicts,
            missing_sections=matching_missing_sections(query, self.store.missing_sections),
        )
