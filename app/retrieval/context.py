"""Context assembly (PROMPT.md §6.6): budgeted, diversified, de-duplicated, conflict partners.

Rules, in order:
  1. walk candidates by final score; skip a chunk if its section already has `max_per_section` blocks, the
     token budget would be exceeded, or its content is already present (every line of it is in a selected
     block — e.g. a table row whose table is selected); stop at `final_k` retrieved blocks;
  2. a selected `table_row` is replaced by its parent table (the table carries the column context and the
     sibling rows; the row would only duplicate one of its lines) — the row is kept if the table does not fit;
  3. an FAQ chunk holding only its question resolves to the canonical full FAQ entry (or is dropped);
  4. a selected chunk that is one side of a known conflict (`conflicts.json`, produced by the corpus
     audit detectors at ingest time) pulls in the other side, so contradictory values are always shown
     together (§3.3-C). Truncated-value findings are NOT conflicts (they are `unclear_value` evidence).
Blocks are numbered [1]..[n] in selection order.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from app.ingest.models import Chunk


@dataclass
class Candidate:
    """A retrieved chunk with its per-stage scores (exposed as retrieval debug info)."""

    idx: int
    chunk: Chunk
    dense: float | None = None
    dense_rank: int | None = None
    bm25: float | None = None
    bm25_rank: int | None = None
    rrf: float = 0.0
    weight: float = 1.0
    rerank: float | None = None
    final: float = 0.0
    reason: str = "retrieved"

    def debug(self) -> dict[str, Any]:
        """JSON-friendly per-stage scores."""
        return {
            "chunk_id": self.chunk.chunk_id,
            "doc_id": self.chunk.doc_id,
            "section_id": self.chunk.section_id,
            "chunk_type": self.chunk.chunk_type,
            "dense": _r(self.dense),
            "dense_rank": self.dense_rank,
            "bm25": _r(self.bm25),
            "bm25_rank": self.bm25_rank,
            "rrf": _r(self.rrf),
            "weight": _r(self.weight),
            "rerank": _r(self.rerank),
            "final": _r(self.final),
            "reason": self.reason,
        }


def _r(value: float | None) -> float | None:
    return None if value is None else round(float(value), 5)


@dataclass
class ConflictGroup:
    """Structural members of one detected conflict, e.g. personal_loans ['4.2', '21']."""

    doc_id: str
    category: str
    members: list[str]
    detail: str = ""

    def matches(self, chunk: Chunk) -> str | None:
        """The member this chunk represents, if any."""
        if chunk.doc_id != self.doc_id:
            return None
        for member in self.members:
            if member.startswith("FAQ:"):
                faq_id = member[4:]
                if chunk.faq_id == faq_id or faq_id in chunk.source_duplicates:
                    return member
            elif chunk.section_id == member and chunk.chunk_type != "faq":
                return member
        return None


#: conflicts.json category -> kind. Only `value_conflict` is a genuine conflict between authoritative values.
CONFLICT_KINDS = {
    "range_conflict": "value_conflict",
    "faq_body_period_mismatch": "value_conflict",
    "faq_omits_condition": "value_conflict",
    "faq_absolute_claim_vs_bounded_table": "value_conflict",
    "scenario_dependent_sla": "scenario_dependent",
    "overlapping_buckets": "ambiguous_overlap",
    "tax_wording_variance": "wording_variance",
    "row_inconsistent_or_truncated": "unclear_value",
}


def conflict_kind(category: str) -> str:
    """Kind of a detected conflict category (unknown categories are treated as genuine conflicts)."""
    return CONFLICT_KINDS.get(category, "value_conflict")


_FAQ_CASE = re.compile(r"\s*\((?:[A-Za-z]+\s+){0,3}(?:case|inquiry|query)\s+\d+\)", re.I)
_FAQ_QUESTION = re.compile(r"^\s*Q\d{3}:")


def display_text(chunk: Chunk) -> str:
    """Chunk text as shown to the LLM and the UI: FAQ question lines lose internal '(… case N)' labels."""
    if chunk.chunk_type != "faq":
        return chunk.text
    lines = chunk.text.split("\n")
    return "\n".join(_FAQ_CASE.sub("", line) if _FAQ_QUESTION.match(line) else line for line in lines)


def faq_parts(chunk: Chunk) -> tuple[str, str]:
    """(question line, first answer paragraph) of an FAQ chunk; answer is '' for an orphan question."""
    lines = [line.strip() for line in display_text(chunk).split("\n") if line.strip()]
    if not lines:
        return "", ""
    answer = next((line for line in lines[1:] if not line.startswith("•")), "")
    return lines[0], answer


def is_orphan_faq(chunk: Chunk) -> bool:
    """An FAQ chunk holding only its question (no answer) — never shown or cited on its own."""
    return chunk.chunk_type == "faq" and not faq_parts(chunk)[1]


def content_lines(chunk: Chunk) -> frozenset[str]:
    """Normalised content lines (table-row title prefixes and bullets removed) for duplicate detection."""
    out = set()
    for raw in display_text(chunk).split("\n"):
        line = raw.split(" — ", 1)[1] if chunk.chunk_type == "table_row" and " — " in raw else raw
        norm = " ".join(line.replace("•", " ").lower().split())
        if norm:
            out.add(norm)
    return frozenset(out)


def resolve_faq(chunk: Chunk, all_chunks: Sequence[Chunk]) -> Chunk | None:
    """The canonical full FAQ entry for an orphan FAQ question (same document, same id / duplicate / question)."""
    if not is_orphan_faq(chunk):
        return chunk
    question = (chunk.question or faq_parts(chunk)[0]).lower()
    for other in all_chunks:
        if other.chunk_type != "faq" or other.doc_id != chunk.doc_id or is_orphan_faq(other):
            continue
        same_id = chunk.faq_id is not None and (
            other.faq_id == chunk.faq_id or chunk.faq_id in other.source_duplicates
        )
        if same_id or (bool(other.question) and (other.question or "").lower() in question):
            return other
    return None


@dataclass
class AssemblyConfig:
    """Assembly limits."""

    final_k: int = 5
    max_per_section: int = 2
    token_budget: int = 2500
    max_conflict_partners: int = 2


@dataclass
class Assembly:
    """Selected blocks in order, plus notes for debugging."""

    blocks: list[Candidate] = field(default_factory=list)
    conflicts: list[ConflictGroup] = field(default_factory=list)


def _partner_chunk(
    member: str, group: ConflictGroup, pool: Sequence[Candidate], all_chunks: Sequence[Chunk]
) -> Chunk | None:
    def is_member(chunk: Chunk) -> bool:
        return (
            chunk.chunk_type != "table_row"
            and ConflictGroup(group.doc_id, group.category, [member]).matches(chunk) is not None
        )

    for cand in pool:  # best-scored retrieved chunk of that member first
        if is_member(cand.chunk):
            return cand.chunk
    return next((c for c in all_chunks if is_member(c)), None)


def assemble(
    candidates: Sequence[Candidate],
    config: AssemblyConfig,
    all_chunks: Sequence[Chunk],
    conflicts: Sequence[ConflictGroup] = (),
    cover_docs: Sequence[str] = (),
    coverage_pool: Sequence[Candidate] = (),
) -> Assembly:
    """Select context blocks from candidates sorted by final score (desc).

    `cover_docs`: documents a multi-document question was routed to; each gets at least its best-scored
    chunk from `coverage_pool` (all fused candidates), so one document cannot crowd out the other.
    """
    by_id = {c.chunk_id: c for c in all_chunks}
    out = Assembly()
    chosen: set[str] = set()
    chosen_lines: set[str] = set()
    per_section: Counter[tuple[str, str]] = Counter()
    tokens = 0

    def add(chunk: Chunk, template: Candidate | None, reason: str, enforce_section_cap: bool) -> bool:
        nonlocal tokens
        key = (chunk.doc_id, chunk.section_id)
        lines = content_lines(chunk)
        if chunk.chunk_id in chosen or (lines and lines <= chosen_lines):
            return False  # same chunk, or every line of it is already in the context
        if enforce_section_cap and per_section[key] >= config.max_per_section:
            return False
        if tokens + chunk.token_count > config.token_budget:
            return False
        cand = (
            template
            if template is not None and template.chunk.chunk_id == chunk.chunk_id
            else Candidate(idx=-1, chunk=chunk)
        )
        cand.reason = reason
        out.blocks.append(cand)
        chosen.add(chunk.chunk_id)
        chosen_lines.update(lines)
        per_section[key] += 1
        tokens += chunk.token_count
        return True

    def template_for(chunk: Chunk) -> Candidate | None:
        return next((c for c in candidates if c.chunk.chunk_id == chunk.chunk_id), None)

    retrieved = 0
    for cand in candidates:
        if retrieved >= config.final_k:
            break
        chunk = cand.chunk
        if is_orphan_faq(chunk):
            resolved = resolve_faq(chunk, all_chunks)
            if resolved is not None and add(resolved, template_for(resolved), "faq_resolved", True):
                retrieved += 1
            continue
        if chunk.chunk_type == "table_row" and chunk.parent_chunk_id in by_id:
            parent = by_id[chunk.parent_chunk_id]
            if parent.chunk_id in chosen:
                continue  # the row is already in the context through its table
            if add(parent, template_for(parent), "table_for_row", enforce_section_cap=True):
                retrieved += 1
                continue
        if add(chunk, cand, "retrieved", enforce_section_cap=True):
            retrieved += 1

    for doc_id in cover_docs:
        if any(b.chunk.doc_id == doc_id for b in out.blocks):
            continue
        best = next(
            (
                c
                for c in coverage_pool
                if c.chunk.doc_id == doc_id
                and c.chunk.chunk_type != "table_row"
                and not is_orphan_faq(c.chunk)
            ),
            None,
        )
        if best is not None:
            add(best.chunk, best, "doc_coverage", enforce_section_cap=False)

    partners_added = 0
    for block in list(out.blocks):
        for group in conflicts:
            if conflict_kind(group.category) == "unclear_value":
                continue  # a truncated value is not a conflict between sources
            member = group.matches(block.chunk)
            if member is None:
                continue
            if group not in out.conflicts:
                out.conflicts.append(group)
            for other in group.members:
                if other == member or partners_added >= config.max_conflict_partners:
                    continue
                partner = _partner_chunk(other, group, candidates, all_chunks)
                if partner is None:
                    continue
                if add(partner, template_for(partner), "conflict_partner", enforce_section_cap=False):
                    partners_added += 1
    return out
