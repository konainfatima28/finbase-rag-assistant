"""Citation parsing/validation and structural source building (PROMPT.md §7.2.2, §3.3-B).

Sources are built ONLY from the metadata of the context blocks the answer actually cites — never from
section numbers written inside answer or FAQ text. Every source carries the document title.

Canonical evidence items (logical sources, stable citation numbers, status) are built in
`app/generation/canonical.py`; no retrieval score or percentage is part of the public contract.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from app.generation.verifier import canonical_pool
from app.ingest.models import Chunk
from app.retrieval.context import display_text, faq_parts
from app.text.tokenize import tokenize

MARKER = re.compile(r"\[(\d{1,2})\]")
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z₹\d•(])|\n+")


@dataclass
class CitationReport:
    """Result of validating the [n] markers in an answer."""

    text: str
    valid: list[int] = field(default_factory=list)
    invalid: list[int] = field(default_factory=list)
    sentences: int = 0
    cited_sentences: int = 0

    @property
    def coverage(self) -> float:
        """Share of factual sentences carrying at least one valid marker."""
        return self.cited_sentences / self.sentences if self.sentences else 0.0


def citation_label(chunk: Chunk) -> str:
    """'<Doc title> — Section 6.2 (p. 3)'; FAQ-only -> 'FAQ Q001'; front matter -> 'Document Information'."""
    if chunk.chunk_type == "faq" and chunk.faq_id:
        where = f"FAQ {chunk.faq_id}"
    elif chunk.section_id == "0":
        where = "Document Information"
    else:
        where = f"Section {chunk.section_id}"
    return f"{chunk.doc_title} — {where} ({chunk.page_label})"


def validate(text: str, n_blocks: int) -> CitationReport:
    """Drop markers that do not map to a provided block; measure per-sentence coverage."""
    found = [int(m) for m in MARKER.findall(text)]
    valid = sorted({n for n in found if 1 <= n <= n_blocks}, key=found.index)
    invalid = sorted({n for n in found if not 1 <= n <= n_blocks})
    cleaned = MARKER.sub(lambda m: m.group(0) if 1 <= int(m.group(1)) <= n_blocks else "", text)
    cleaned = re.sub(r"[ \t]+([.,;:])", r"\1", cleaned).strip()
    sentences = [s for s in _SENTENCE.split(cleaned) if re.search(r"[A-Za-zऀ-ॿ]{3}", s)]
    factual = [s for s in sentences if not _is_non_factual(s)]
    cited = [s for s in factual if MARKER.search(s)]
    return CitationReport(
        text=cleaned, valid=valid, invalid=invalid, sentences=len(factual), cited_sentences=len(cited)
    )


def _is_non_factual(sentence: str) -> bool:
    """Courtesy / handoff sentences that need no citation."""
    return bool(
        re.match(
            r"^\s*(please|you can contact|for (further|more) (help|details)|hope this|let me know|i'?m sorry)",
            sentence,
            re.I,
        )
    )


def best_snippet(chunk: Chunk, answer: str, max_chars: int = 320) -> str:
    """The chunk line(s) most similar to the answer — highlighted in the UI. FAQ: question + its answer."""
    if chunk.chunk_type == "faq":
        question, faq_answer = faq_parts(chunk)
        text = f"{question} {faq_answer}".strip()
        return text if len(text) <= max_chars else text[: max_chars - 1] + "…"
    answer_tokens = set(tokenize(answer))
    lines = [line for line in display_text(chunk).split("\n") if line.strip()]
    if not lines:
        return ""
    answer_figures = canonical_pool([answer]) if answer else set()

    def score(line: str) -> tuple[int, int, int]:  # shared figures first, then shared words, then position
        return (
            len(answer_figures & canonical_pool([line])),
            len(answer_tokens & set(tokenize(line))),
            -lines.index(line),
        )

    best = max(lines, key=score)
    return best if len(best) <= max_chars else best[: max_chars - 1] + "…"


class _Cited(Protocol):
    citation: str
    role: str


def source_line(sources: Sequence[_Cited]) -> str:
    """'Source: A — Section 6.2 (p. 3); B — FAQ Q001 (p. 9)' — primary first, deduplicated, citation order."""
    ordered = [s for s in sources if s.role == "primary"] + [s for s in sources if s.role != "primary"]
    seen: list[str] = []
    for source in ordered:
        if source.citation not in seen:
            seen.append(source.citation)
    return "Source: " + "; ".join(seen) if seen else ""
