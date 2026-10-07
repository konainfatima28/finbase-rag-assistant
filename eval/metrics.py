"""Evaluation metrics (PROMPT.md §11.2). Pure functions, unit-tested; LLM-judge metrics live in judge.py.

Retrieval is scored at SECTION level (so de-duplication never hurts): a retrieved chunk is relevant if it
belongs to a gold section (sub-sections count for their parent), a gold FAQ id (incl. merged duplicates),
or a templated boilerplate copy listed in its `source_duplicates`. `gold_mode="any"` items have
alternative gold sections (finding one is enough); `gold_mode="all"` items need every listed section.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from app.generation.verifier import canonical_pool, extract_figures
from app.ingest.models import Chunk

ABSTAIN_PHRASES = re.compile(
    r"couldn't find this|could not find this|not (?:available|found|included|covered|provided|specified|mentioned) in "
    r"(?:\w+\s){0,3}(?:knowledge base|documents?|sop|policy)|"
    r"not (?:fully )?(?:detailed|described) in (?:\w+\s){0,3}(?:knowledge base|documents?)|"
    # the DOCUMENTS lack it ("the SOP does not specify ...") - not "FinBase does not provide X" (unsupported service)
    r"(?:documents?|sop|policy|manual|guidelines|knowledge base|source|it|they)\s+(?:do|does)(?:n't| not)\s+"
    r"(?:specify|mention|state|contain|include|list)|"
    r"is not specified|isn't specified|no information (?:about|on)|cannot provide",
    re.I,
)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
REFUTATION = re.compile(
    r"pretend|you asked|you said|you mentioned|you claimed|hypothetical|regardless|rather than|instead of|"
    r"is not 0|isn't 0|not 0%|cannot be waived|does not waive|not waived",
    re.I,
)


@dataclass(frozen=True)
class Gold:
    """One gold unit."""

    doc_id: str
    section_id: str

    def matches(self, chunk: Chunk) -> bool:
        """Section-level relevance of a chunk."""
        if chunk.doc_id != self.doc_id:
            return False
        if self.section_id.startswith("FAQ:"):
            faq = self.section_id[4:]
            return chunk.faq_id == faq or (chunk.chunk_type == "faq" and faq in chunk.source_duplicates)
        if chunk.chunk_type == "faq":
            return False
        if chunk.section_id == self.section_id or chunk.section_id.startswith(f"{self.section_id}."):
            return True
        return f"Section {self.section_id}" in chunk.source_duplicates


def golds(item: dict[str, Any]) -> list[Gold]:
    """Gold units of a golden item."""
    return [Gold(g["doc_id"], g["section_id"]) for g in item.get("gold_sections", [])]


def needed(item: dict[str, Any]) -> int:
    """How many distinct gold units constitute full recall."""
    units = golds(item)
    if not units:
        return 0
    return len(units) if item.get("gold_mode", "any") == "all" else 1


def _matched_units(ranked: Sequence[Chunk], units: list[Gold]) -> list[int | None]:
    """For each ranked chunk, index of a NEW gold unit it satisfies (each unit counted once), else None."""
    seen: set[int] = set()
    out: list[int | None] = []
    for chunk in ranked:
        hit = next((i for i, g in enumerate(units) if i not in seen and g.matches(chunk)), None)
        if hit is not None:
            seen.add(hit)
        out.append(hit)
    return out


def retrieval_metrics(
    item: dict[str, Any], ranked: Sequence[Chunk], context: Sequence[Chunk], ks: Iterable[int] = (1, 3, 5, 10)
) -> dict[str, float] | None:
    """Hit@k, Recall@k, MRR, nDCG@10, context precision, noise ratio (None when the item has no gold)."""
    units = golds(item)
    if not units:
        return None
    need = needed(item)
    marks = _matched_units(ranked, units)
    out: dict[str, float] = {}
    for k in ks:
        found = sum(1 for m in marks[:k] if m is not None)
        out[f"hit@{k}"] = 1.0 if found else 0.0
        out[f"recall@{k}"] = min(found, need) / need
    first = next((i for i, m in enumerate(marks) if m is not None), None)
    out["mrr"] = 1.0 / (first + 1) if first is not None else 0.0
    dcg = sum(1.0 / math.log2(i + 2) for i, m in enumerate(marks[:10]) if m is not None and i < 10)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(min(need if need > 1 else 1, 10)))
    out["ndcg@10"] = min(1.0, dcg / idcg) if idcg else 0.0
    relevant_ctx = sum(1 for c in context if any(g.matches(c) for g in units))
    out["context_precision"] = relevant_ctx / len(context) if context else 0.0
    out["noise_ratio"] = 1.0 - out["context_precision"] if context else 1.0
    return out


# ------------------------------------------------------------------ answer correctness
def _fact_present(fact: str, answer: str, pool: set[str]) -> bool:
    for alt in (a.strip() for a in fact.split("|")):
        if not alt:
            continue
        if alt.lower() in answer.lower():
            return True
        figs = extract_figures(alt)
        if figs and all(f.canonical in pool for f in figs):
            return True
    return False


def key_fact_recall(expected: Sequence[str], answer: str) -> float | None:
    """Share of expected atomic facts present (normalised numbers or case-insensitive text; 'a|b' = either)."""
    if not expected:
        return None
    pool = canonical_pool([answer])
    return sum(1 for fact in expected if _fact_present(fact, answer, pool)) / len(expected)


def forbidden_violations(forbidden: Sequence[str], answer: str) -> list[str]:
    """Forbidden facts ASSERTED in the answer. '%' alone means 'any percentage'. Sentences that quote or
    refute the user ("you asked to pretend ... 0%, but ...") are not assertions; figures are compared on
    recognised figures only (a verbatim garbled '₹1,00,0' is not '₹1,000')."""
    hits: list[str] = []
    answer = " ".join(x for x in _SENTENCE_SPLIT.split(answer) if not REFUTATION.search(x))
    pool = {f.canonical for f in extract_figures(answer)}
    for fact in forbidden:
        if fact == "%":
            if any(f.kind == "percent" for f in extract_figures(answer)):
                hits.append(fact)
            continue
        figs = extract_figures(fact)
        pure_figure = bool(figs) and re.fullmatch(r"[\s₹Rs.,\d%+TL-]+", fact) is not None
        if (pure_figure and all(f.canonical in pool for f in figs)) or (
            not pure_figure and fact.lower() in answer.lower()
        ):
            hits.append(fact)
    return hits


def abstained(result: dict[str, Any]) -> bool:
    """The system declined to answer (gate / NOT_FOUND / explicit 'not in the documents')."""
    answer = str(result.get("answer", "")).strip()
    first = _SENTENCE_SPLIT.split(answer, maxsplit=1)[0] if answer else ""
    return (not result.get("answerable", False)) or bool(ABSTAIN_PHRASES.search(first))


def prf(tp: int, fp: int, fn: int) -> dict[str, float]:
    """Precision / recall / F1."""
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4)}


def abstention_metrics(rows: Sequence[tuple[bool, bool]]) -> dict[str, float]:
    """rows = (should_abstain, did_abstain). Positive class = abstain."""
    tp = sum(1 for s, d in rows if s and d)
    fp = sum(1 for s, d in rows if not s and d)
    fn = sum(1 for s, d in rows if s and not d)
    answerable = [d for s, d in rows if not s]
    return {
        **prf(tp, fp, fn),
        "over_refusal": round(sum(answerable) / len(answerable), 4) if answerable else 0.0,
        "n_unanswerable": float(sum(1 for s, _ in rows if s)),
    }


# ------------------------------------------------------------------ citations
def supports_claim(chunk: Chunk, item: dict[str, Any], answer: str) -> bool:
    """The cited chunk contains at least one expected fact that the answer also states."""
    facts = [f for f in item.get("expected_facts", []) if _fact_present(f, answer, canonical_pool([answer]))]
    pool = canonical_pool([chunk.embed_text])
    return any(_fact_present(f, chunk.embed_text, pool) for f in facts)


def citation_metrics(
    item: dict[str, Any], sources: Sequence[dict[str, Any]], chunks_by_id: dict[str, Chunk], answer: str = ""
) -> dict[str, float] | None:
    """Citation precision ("cited sections that are gold or contain the claim", PROMPT.md 11.2), recall vs
    gold sections, plus snippet-exists and structural-metadata checks.

    One source = one canonical evidence item, which may span several chunks of the same logical source
    (`chunk_ids`, Phase 2); it counts once, and is relevant if any of its chunks is."""
    units = golds(item)
    if not sources:
        return None
    groups = [
        [chunks_by_id[cid] for cid in s.get("chunk_ids", [s["chunk_id"]]) if cid in chunks_by_id]
        for s in sources
    ]
    groups = [g for g in groups if g]
    snippet_ok = [
        any(
            s["snippet"].rstrip("…") in chunks_by_id[cid].text
            for cid in s.get("chunk_ids", [s["chunk_id"]])
            if cid in chunks_by_id
        )
        for s in sources
        if s.get("snippet")
    ]
    structural = [
        (s["section_id"], s["doc_title"], s["page_start"]) == (c.section_id, c.doc_title, c.page_start)
        and c.doc_title in s["citation"]
        for s in sources
        if (c := chunks_by_id.get(s["chunk_id"])) is not None
    ]
    out: dict[str, float] = {
        "snippet_exists": sum(snippet_ok) / len(snippet_ok) if snippet_ok else 1.0,
        "structural_match": sum(structural) / len(structural) if structural else 0.0,
    }
    if units:
        relevant = sum(
            1
            for g in groups
            if any(any(u.matches(c) for u in units) or supports_claim(c, item, answer) for c in g)
        )
        out["precision"] = relevant / len(groups) if groups else 0.0
        found = len({i for g in groups for c in g for i, u in enumerate(units) if u.matches(c)})
        out["recall"] = min(found, needed(item)) / needed(item)
    return out


# ------------------------------------------------------------------ aggregation helpers
def mean(values: Iterable[float | None]) -> float | None:
    """Mean ignoring None; None if empty."""
    vals = [v for v in values if v is not None]
    return round(sum(vals) / len(vals), 4) if vals else None


def percentile(values: Sequence[float], q: float) -> float | None:
    """Nearest-rank percentile."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(q / 100 * len(ordered)))
    return round(ordered[min(rank, len(ordered)) - 1], 1)
