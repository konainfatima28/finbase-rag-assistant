"""Canonical evidence & citation layer (Phase 2): deterministic, between generation and the API.

The LLM still sees the Phase-1 context blocks [1]..[n] (retrieval order) and cites those numbers. After all
Phase-1 safety post-processing, this module turns the cited blocks into canonical evidence:

  * **Logical identity** (`evidence_id`): `<doc_id>:faq:<Qnnn>` for an FAQ entry, `<doc_id>:section:<id>` for
    everything else. A table row and its table, overlapping chunks of one section, an orphan FAQ question
    and its full entry all collapse into ONE evidence item. Different sections stay different items, so the
    two sides of a conflict (e.g. loans §21 vs §4.2) are never merged.
  * **Citation numbers** 1..k follow the order in which the answer first cites each evidence item; the
    answer's `[n]` markers are rewritten to them (a run like `[2][3]` that points at one item becomes `[1]`).
    Same answer + same evidence => same numbers, whatever the retrieval order was.
  * **Status** per item: `conflicting_sources` (a member of a code-detected conflict), `unclear_value` (a row
    the request is about holds a truncated value; listed in `unclear_rows`), otherwise `normal`.
  * **Scope**: whether the item's product is one the question names (`in_scope`), cross-cutting (KYC,
    `general`), another product (`other_product`) or the question names none (`unspecified`).
  * **Claims**: each answer sentence with the evidence ids / citation numbers it cites.

No retrieval scores or percentages are part of an evidence item.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from app.generation.citations import MARKER, best_snippet, citation_label
from app.generation.evidence import (
    CONFLICTING_SOURCES,
    PRODUCT_TERMS,
    UNCLEAR_VALUE,
    ConflictEvidence,
    UnclearValue,
    named_products,
)
from app.ingest.models import Chunk
from app.retrieval.context import Candidate, faq_parts, is_orphan_faq
from app.text.numbers import find_malformed_amounts

NORMAL = "normal"
#: short product names (document identity) used in human-readable evidence labels
PRODUCT_NAMES = {
    "personal_loans": "Personal Loans",
    "credit_cards": "Credit Cards",
    "savings_account": "Savings Account",
    "payments_upi": "UPI Payments",
    "fd_wealth": "FD & Wealth",
    "kyc_security": "KYC & Security",
}
SOURCE_TYPES = {
    "policy": "section",
    "boilerplate": "section",
    "annex": "annex",
    "table": "table",
    "table_row": "row",
    "faq": "faq",
}
_RUN = re.compile(r"(?:\[\d{1,2}\])+")
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z₹\d•(*-])|\n+")


def product_name(chunk: Chunk) -> str:
    """'Personal Loans' (falls back to the document title)."""
    return PRODUCT_NAMES.get(chunk.doc_id, chunk.doc_title)


def evidence_key(chunk: Chunk) -> str:
    """Logical source identity (stable across requests and retrieval orders)."""
    if chunk.chunk_type == "faq" and chunk.faq_id:
        return f"{chunk.doc_id}:faq:{chunk.faq_id}"
    return f"{chunk.doc_id}:section:{chunk.section_id}"


def evidence_label(chunk: Chunk) -> str:
    """'Personal Loans — Section 4.2: Upfront Processing Charges' / 'Credit Cards — FAQ Q007'.
    Built only from chunk metadata (real section titles), never from answer text."""
    product = product_name(chunk)
    if chunk.chunk_type == "faq" and chunk.faq_id:
        return f"{product} — FAQ {chunk.faq_id}"
    if chunk.section_id == "0":
        return f"{product} — Document Information"
    return f"{product} — Section {chunk.section_id}: {chunk.section_title}"


@dataclass
class EvidenceItem:
    """One canonical evidence item (the public evidence/source contract)."""

    evidence_id: str
    n: int | None
    label: str
    citation: str
    doc_id: str
    doc_title: str
    doc_code: str
    product: str
    section_id: str
    section_title: str
    page_start: int
    page_end: int
    source_type: str
    chunk_type: str
    chunk_id: str
    chunk_ids: list[str]
    snippet: str
    status: str = NORMAL
    cited: bool = True
    role: str = "primary"
    scope: str = "unspecified"
    faq_id: str | None = None
    faq_question: str | None = None
    quality_flag: str = "ok"
    unclear_rows: list[dict[str, Any]] = field(default_factory=list)
    conflict_ids: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """JSON-friendly."""
        return asdict(self)


@dataclass
class EvidenceSet:
    """Canonical evidence for one answer."""

    answer: str
    items: list[EvidenceItem]
    claims: list[dict[str, Any]]
    conflicts: list[dict[str, Any]]
    unclear_values: list[dict[str, Any]]

    @property
    def cited(self) -> list[EvidenceItem]:
        """Cited items in citation order (the legacy `sources` view)."""
        return sorted((i for i in self.items if i.n is not None), key=lambda i: i.n or 0)

    @property
    def statuses(self) -> list[str]:
        """Answer-level evidence statuses (unclear first, as in Phase 1)."""
        out = [UNCLEAR_VALUE] if self.unclear_values else []
        return out + ([CONFLICTING_SOURCES] if self.conflicts else [])

    def as_dict(self) -> dict[str, Any]:
        """The `evidence` object of the API response."""
        return {
            "statuses": self.statuses,
            "items": [i.as_dict() for i in self.items],
            "claims": self.claims,
            "unclear_values": self.unclear_values,
            "conflicts": self.conflicts,
        }


def scope_of(chunk: Chunk, named: set[str]) -> str:
    """Product scope of an evidence item relative to the products the question names."""
    if not named:
        return "unspecified"
    if chunk.doc_id in named:
        return "in_scope"
    return "other_product" if chunk.doc_id in PRODUCT_TERMS else "general"


def primary_chunk(group: Sequence[Chunk], cited: Sequence[bool]) -> Chunk:
    """The chunk that represents a logical source: a full FAQ entry over an orphan question, a table over one
    of its rows, then a cited chunk over an uncited one, then context order."""
    ranked = sorted(
        range(len(group)),
        key=lambda i: (is_orphan_faq(group[i]), group[i].chunk_type == "table_row", not cited[i], i),
    )
    return group[ranked[0]]


def renumber(answer: str, mapping: dict[int, int]) -> str:
    """Rewrite block markers to citation numbers; a marker run keeps each number once (first-seen order)."""

    def run(match: re.Match[str]) -> str:
        numbers = [mapping[int(n)] for n in re.findall(r"\d+", match.group(0)) if int(n) in mapping]
        return "".join(f"[{n}]" for n in dict.fromkeys(numbers))

    return _RUN.sub(run, answer)


def claims_of(answer: str, ids_by_n: dict[int, str]) -> list[dict[str, Any]]:
    """Each cited answer sentence -> the evidence it cites (deterministic, from the markers)."""
    out = []
    for sentence in _SENTENCE.split(answer):
        numbers = list(dict.fromkeys(int(n) for n in MARKER.findall(sentence) if int(n) in ids_by_n))
        if numbers:
            out.append(
                {
                    "text": re.sub(r"\s+([.,;:!?])", r"\1", MARKER.sub("", sentence)).strip(),
                    "citations": numbers,
                    "evidence_ids": [ids_by_n[n] for n in numbers],
                }
            )
    return out


def build_evidence(
    answer: str,
    blocks: Sequence[Candidate],
    unclear: Sequence[UnclearValue] = (),
    conflicts: Sequence[ConflictEvidence] = (),
    question: str = "",
) -> EvidenceSet:
    """Canonical, de-duplicated, stably numbered evidence for `answer` (whose markers are block numbers)."""
    chunks = [b.chunk for b in blocks]
    key_by_block = {n: evidence_key(c) for n, c in enumerate(chunks, start=1)}
    members: dict[str, list[int]] = {}
    for n, key in key_by_block.items():
        members.setdefault(key, []).append(n)

    # citation numbers: order of first citation in the answer
    order: list[str] = []
    for marker in MARKER.findall(answer):
        found = key_by_block.get(int(marker))
        if found is not None and found not in order:
            order.append(found)
    number = {key: i for i, key in enumerate(order, start=1)}
    mapping = {n: number[key] for n, key in key_by_block.items() if key in number}
    final = renumber(answer, mapping)

    # statuses
    chunk_key = {c.chunk_id: evidence_key(c) for c in chunks}
    rows: dict[str, list[dict[str, Any]]] = {}
    for value in unclear:
        rows.setdefault(chunk_key[value.chunk_id], []).append(value.as_dict())
    conflict_of: dict[str, list[str]] = {}
    conflict_records = []
    for conflict in conflicts:
        cid = f"{conflict.group.doc_id}:{conflict.group.category}:{'+'.join(conflict.group.members)}"
        keys = [key_by_block[n] for n in conflict.blocks.values()]
        for key in keys:
            conflict_of.setdefault(key, []).append(cid)
        docs = {chunks[n - 1].doc_id for n in conflict.blocks.values()}
        record = conflict.as_dict()
        sides = " and ".join(conflict.sections)
        record.update(
            {
                "conflict_id": cid,
                "evidence_ids": list(dict.fromkeys(keys)),
                "citations": [number[k] for k in dict.fromkeys(keys) if k in number],
                "scope": "same_document" if len(docs) == 1 else "cross_document",
                "same_document": len(docs) == 1,
                "description": (
                    f"{sides} of the {conflict.doc_title} state different values."
                    if len(docs) == 1
                    else "Different documents state different values."
                ),
            }
        )
        conflict_records.append(record)

    named = named_products(question)
    keys = order + [
        k for k in dict.fromkeys(key_by_block.values()) if k not in number and (k in rows or k in conflict_of)
    ]
    items: list[EvidenceItem] = []
    for key in keys:
        group = [chunks[n - 1] for n in members[key]]
        primary = primary_chunk(group, [n in mapping and f"[{n}]" in answer for n in members[key]])
        item_rows = rows.get(key, [])
        status = CONFLICTING_SOURCES if key in conflict_of else UNCLEAR_VALUE if item_rows else NORMAL
        question_text, _ = faq_parts(primary) if primary.chunk_type == "faq" else ("", "")
        items.append(
            EvidenceItem(
                evidence_id=key,
                n=number.get(key),
                label=evidence_label(primary),
                citation=citation_label(primary),
                doc_id=primary.doc_id,
                doc_title=primary.doc_title,
                doc_code=primary.doc_code,
                product=product_name(primary),
                section_id=primary.section_id,
                section_title=primary.section_title,
                page_start=min(c.page_start for c in group),
                page_end=max(c.page_end for c in group),
                source_type=SOURCE_TYPES.get(primary.chunk_type, primary.chunk_type),
                chunk_type=primary.chunk_type,
                chunk_id=primary.chunk_id,
                chunk_ids=[c.chunk_id for c in group],
                snippet=best_snippet(primary, final),
                status=status,
                cited=key in number,
                scope=scope_of(primary, named),
                faq_id=primary.faq_id,
                faq_question=re.sub(r"^Q\d{3}:\s*", "", question_text) or None,
                quality_flag="suspect_value" if item_rows else "ok",
                unclear_rows=item_rows,
                conflict_ids=conflict_of.get(key, []),
            )
        )
    if any(i.cited and i.chunk_type != "faq" for i in items):
        for item in items:
            if item.chunk_type == "faq":
                item.role = "secondary"

    ids_by_n = {i.n: i.evidence_id for i in items if i.n is not None}
    unclear_records = [
        {
            **value.as_dict(),
            "evidence_id": chunk_key[value.chunk_id],
            "n": number.get(chunk_key[value.chunk_id]),
        }
        for value in unclear
    ]
    return EvidenceSet(final, items, claims_of(final, ids_by_n), conflict_records, unclear_records)


def related_items(blocks: Sequence[Candidate], question: str, limit: int = 3) -> list[dict[str, Any]]:
    """Related-topic cards (abstentions): canonical items, de-duplicated, never another product's evidence
    when the question names a product. `n` is 0 (not cited)."""
    named = named_products(question)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for block in blocks:
        chunk = block.chunk
        key = evidence_key(chunk)
        if key in seen or scope_of(chunk, named) == "other_product":
            continue
        seen.add(key)
        question_text, _ = faq_parts(chunk) if chunk.chunk_type == "faq" else ("", "")
        snippet = best_snippet(chunk, "")
        item = EvidenceItem(
            evidence_id=key,
            n=0,
            label=evidence_label(chunk),
            citation=citation_label(chunk),
            doc_id=chunk.doc_id,
            doc_title=chunk.doc_title,
            doc_code=chunk.doc_code,
            product=product_name(chunk),
            section_id=chunk.section_id,
            section_title=chunk.section_title,
            page_start=chunk.page_start,
            page_end=chunk.page_end,
            source_type=SOURCE_TYPES.get(chunk.chunk_type, chunk.chunk_type),
            chunk_type=chunk.chunk_type,
            chunk_id=chunk.chunk_id,
            chunk_ids=[chunk.chunk_id],
            snippet=snippet,
            cited=False,
            role="related",
            scope=scope_of(chunk, named),
            faq_id=chunk.faq_id,
            faq_question=re.sub(r"^Q\d{3}:\s*", "", question_text) or None,
            quality_flag="suspect_value" if find_malformed_amounts(snippet) else "ok",
        )
        out.append(item.as_dict())
        if len(out) >= limit:
            break
    return out
