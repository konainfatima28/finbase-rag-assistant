"""De-duplication (PROMPT.md §5.4).

Text is normalised by masking counters / IDs / clause numbers, then hashed. One canonical chunk per
(document, chunk type, masked hash) is kept; every merged member is recorded in `source_duplicates`.
FAQs are grouped by masked *question*; if one question has different masked answers, every distinct
answer is kept and reported as a conflict. Canonical copies keep their full original text, so the
protocol/response-code tables (U16/U30/U69/U88, ...) survive with all values.
"""

from __future__ import annotations

import hashlib
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from app.ingest.models import Chunk

_MASKS: list[tuple[re.Pattern[str], str]] = [
    # ID-like tokens ending in a number: FB-OPS-007, PL-VAL-0001, PAY-ERR-701, SEC-LOG-0100, SAV-CAT-02, PL-02
    (re.compile(r"\b[a-z]{1,6}(?:-[a-z0-9]{1,8})*-\d+[a-z]?\b"), "<id>"),
    # counters after labels: "case 37", "inquiry 4", "node 4", "clause 7.1", "rule 4.2", "level 2", "section 7"
    (
        re.compile(
            r"\b(case|inquiry|query|node|level|clause|rule|protocol|guideline|standard|section|stream|job|items?|"
            r"protocol|annexure)\s+\d+(?:\.\d+)?(?:\s*-\s*\d+)?"
        ),
        r"\1 #",
    ),
    (re.compile(r"^q\d{3}:", re.M), "q#:"),
    (re.compile(r"\s+"), " "),
]


def mask_text(text: str) -> str:
    """Lower-case text with counters/IDs masked (used only for hashing, never shown)."""
    masked = text.lower()
    for pattern, repl in _MASKS:
        masked = pattern.sub(repl, masked)
    return masked.strip()


def text_hash(text: str) -> str:
    """sha1 of the masked text."""
    return hashlib.sha1(mask_text(text).encode("utf-8")).hexdigest()


def _member_label(chunk: Chunk) -> str:
    return chunk.faq_id if chunk.chunk_type == "faq" and chunk.faq_id else f"Section {chunk.section_id}"


@dataclass
class DedupResult:
    """Kept chunks plus a JSON-serialisable report."""

    chunks: list[Chunk]
    report: dict[str, Any] = field(default_factory=dict)


def deduplicate(chunks: list[Chunk]) -> DedupResult:
    """Collapse near-duplicate chunks per document (see module docstring)."""
    groups: OrderedDict[tuple[str, ...], list[Chunk]] = OrderedDict()
    for chunk in chunks:
        if chunk.chunk_type == "faq":
            key: tuple[str, ...] = (
                chunk.doc_id,
                "faq",
                mask_text(chunk.question or ""),
                text_hash(chunk.text),
            )
        else:
            key = (chunk.doc_id, chunk.chunk_type, text_hash(chunk.text))
        groups.setdefault(key, []).append(chunk)

    kept: list[Chunk] = []
    remap: dict[str, str] = {}
    report_groups: list[dict[str, Any]] = []
    for members in groups.values():
        canonical = members[0]
        duplicates = [_member_label(m) for m in members[1:]]
        canonical = canonical.model_copy(update={"source_duplicates": duplicates})
        kept.append(canonical)
        for member in members:
            remap[member.chunk_id] = canonical.chunk_id
        if len(members) > 1:
            report_groups.append(
                {
                    "doc_id": canonical.doc_id,
                    "chunk_type": canonical.chunk_type,
                    "canonical_chunk_id": canonical.chunk_id,
                    "canonical": _member_label(canonical),
                    "copies": len(members),
                    "members": [_member_label(m) for m in members],
                    "preview": canonical.text[:120],
                }
            )

    kept = [
        c.model_copy(update={"parent_chunk_id": remap.get(c.parent_chunk_id, c.parent_chunk_id)})
        if c.parent_chunk_id
        else c
        for c in kept
    ]
    conflicts = _faq_conflicts(kept)
    report = {
        "input_chunks": len(chunks),
        "kept_chunks": len(kept),
        "removed": len(chunks) - len(kept),
        "by_doc": _by_doc(chunks, kept),
        "faq_answer_conflicts": conflicts,
        "groups": report_groups,
    }
    return DedupResult(chunks=kept, report=report)


def _faq_conflicts(kept: list[Chunk]) -> list[dict[str, Any]]:
    """Same masked question kept more than once => answers differ => conflict."""
    seen: dict[tuple[str, str], list[Chunk]] = {}
    for chunk in kept:
        if chunk.chunk_type == "faq":
            seen.setdefault((chunk.doc_id, mask_text(chunk.question or "")), []).append(chunk)
    return [
        {"doc_id": doc_id, "question": items[0].question, "faq_ids": [c.faq_id for c in items]}
        for (doc_id, _q), items in seen.items()
        if len(items) > 1
    ]


def _by_doc(before: list[Chunk], after: list[Chunk]) -> dict[str, dict[str, dict[str, int]]]:
    out: dict[str, dict[str, dict[str, int]]] = {}
    for label, items in (("before", before), ("after", after)):
        for chunk in items:
            doc = out.setdefault(chunk.doc_id, {"before": {}, "after": {}})
            doc[label][chunk.chunk_type] = doc[label].get(chunk.chunk_type, 0) + 1
    return out


def assert_no_duplicates(chunks: list[Chunk]) -> None:
    """Invariant: no two chunks of the same document share normalised (masked) text."""
    seen: dict[tuple[str, str], str] = {}
    for chunk in chunks:
        key = (chunk.doc_id, mask_text(chunk.text))
        if key in seen:
            raise AssertionError(f"duplicate normalised text: {seen[key]} and {chunk.chunk_id}")
        seen[key] = chunk.chunk_id
