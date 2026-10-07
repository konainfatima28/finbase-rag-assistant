"""Prompt templates (versioned files in `prompts/`) and message construction (PROMPT.md §7.1).

The system prompt is used verbatim. The user turn follows the §7.1 template (CONTEXT blocks, then QUESTION)
plus a short NOTES list derived deterministically from the pipeline (DECISIONS D-024): detected conflicts,
TOC sections missing from the body, eligibility requests, and a fixed source-attribution reminder.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.generation.evidence import unclear_values_in
from app.providers.base import ChatMessage
from app.retrieval.context import Candidate, ConflictGroup, conflict_kind, display_text
from app.safety.injection import neutralize_context

NOT_FOUND = "NOT_FOUND"
ATTRIBUTION_NOTE = (
    "Each block header names its source document. Apply a figure only to the product and document it is "
    "stated for; never transfer a fee or limit from one product to another."
)
_ELIGIBILITY = re.compile(
    r"\b(approve|approval|sanction|am i eligible|eligib|do i qualify|will i get)\b", re.I
)


@dataclass(frozen=True)
class Prompts:
    """Loaded templates."""

    answer_system: str
    rewrite_system: str
    version: str


@lru_cache(maxsize=4)
def load_prompts(prompts_dir: Path, version: str) -> Prompts:
    """Read templates once per process."""
    return Prompts(
        answer_system=(prompts_dir / "answer_system.txt").read_text(encoding="utf-8").strip(),
        rewrite_system=(prompts_dir / "rewrite_system.txt").read_text(encoding="utf-8").strip(),
        version=version,
    )


def context_block(number: int, block: Candidate) -> str:
    """'[n] <contextual header>\\n<text>'; instruction-like lines in a chunk are neutralised."""
    return f"[{number}] {block.chunk.header}\n{neutralize_context(display_text(block.chunk))}"


def _future_years(question: str, blocks: Sequence[Candidate]) -> list[str]:
    """Years asked about that lie after every document's effective date (e.g. 'rates in 2027')."""
    effective = [int(y) for b in blocks for y in re.findall(r"\b(20\d{2})\b", b.chunk.effective_date)]
    if not effective:
        return []
    latest = max(effective)
    asked = sorted({y for y in re.findall(r"\b(20\d{2})\b", question) if int(y) > latest})
    return [y for y in asked if f" {y}" not in " ".join(b.chunk.text for b in blocks)]


def context_notes(
    blocks: Sequence[Candidate],
    conflicts: Sequence[ConflictGroup],
    missing_sections: Sequence[dict[str, str]],
    question: str,
) -> list[str]:
    """Deterministic guidance derived from the corpus audit and the request."""
    notes: list[str] = []
    for group in conflicts:
        numbers = sorted({i for i, b in enumerate(blocks, start=1) if group.matches(b.chunk)})
        if not numbers:
            continue
        kind = conflict_kind(group.category)
        refs = " and ".join(f"[{n}]" for n in numbers)
        detail = group.detail or group.category
        if kind == "value_conflict" and len(numbers) >= 2:
            title = blocks[numbers[0] - 1].chunk.doc_title
            notes.append(
                f"Blocks {refs} are different parts of the same document ({title}) and give different values for "
                f"the same item ({detail}). Present every value with its citation and say that these sections of "
                "the same document differ (do not call them different documents). Do not pick one value and do not "
                "average or combine them; suggest confirming with FinBase support."
            )
        elif kind == "scenario_dependent":
            notes.append(
                f"Blocks {refs} state different values or conditions for the same item ({detail}). Present every "
                "value with its citation and say that they depend on the scenario; do not pick one."
            )
        elif kind == "wording_variance":
            notes.append(
                f"Blocks {refs} word the same charge slightly differently ({detail}). Quote the amount as written, "
                "with citations."
            )
        else:
            notes.append(
                f"Block(s) {refs} contain entries that overlap or look inconsistent ({detail}). "
                "Point out the ambiguity and give each applicable value instead of choosing one."
            )
    for i, block in enumerate(blocks, start=1):
        for value in unclear_values_in(block.chunk):
            notes.append(
                f"In block [{i}], {value.describe()} is incomplete or garbled in the source. Do not repeat, complete, "
                "round or guess that figure; say that the exact value cannot be safely determined from FinBase's "
                "documents, and still give the complete values of that row (e.g. its other limits)."
            )
    block_words = set(re.findall(r"[a-z]{4,}", " ".join(b.chunk.embed_text for b in blocks).lower()))
    question_words = set(re.findall(r"[a-z]{4,}", question.lower()))
    for section in missing_sections:
        title_words = set(re.findall(r"[a-z]{4,}", section["title"].lower()))
        uncovered = sorted((title_words & question_words) - block_words)
        if uncovered:  # the question asks for exactly what the missing section would contain
            notes.append(
                f"'{section['doc_title']}' lists Section {section['section_id']} '{section['title']}' in its table of "
                f"contents, but that section's text is missing, and no block covers '{' '.join(uncovered)}'. Begin the "
                "reply by saying that this information is not available in the knowledge base; then you may give "
                "related facts from the blocks, clearly presented as related information, not as the answer."
            )
            continue
        notes.append(
            f"'{section['doc_title']}' lists Section {section['section_id']} '{section['title']}' in its table of "
            "contents, but that section's own text is missing from the document. Answer from the other CONTEXT "
            "blocks if they cover the question; for any part they do not cover, say that it is not available in "
            "the knowledge base instead of inferring it."
        )
    future = _future_years(question, blocks)
    if future:
        notes.append(
            f"The customer asks about {', '.join(future)}, but the documents are effective from "
            f"{blocks[0].chunk.effective_date} and state no values for {', '.join(future)}. Begin the reply by saying "
            f"that values for {', '.join(future)} are not available in the knowledge base; you may then give the "
            "currently published values, clearly labelled as current."
        )
    if _ELIGIBILITY.search(question):
        notes.append(
            "The customer asks for an approval or eligibility decision. Do not decide (neither approve nor reject); "
            "explain the published criteria and suggest applying or contacting FinBase."
        )
    by_doc: dict[str, list[int]] = {}
    for i, block in enumerate(blocks, start=1):
        by_doc.setdefault(block.chunk.doc_title, []).append(i)
    if len(by_doc) > 1:
        groups = "; ".join(f"{''.join(f'[{n}]' for n in nums)} = {title}" for title, nums in by_doc.items())
        notes.append(f"Blocks come from different documents: {groups}. {ATTRIBUTION_NOTE}")
    else:
        notes.append(ATTRIBUTION_NOTE)
    return notes


def user_turn(
    blocks: Sequence[Candidate],
    question: str,
    interpreted_as: str | None = None,
    notes: Sequence[str] = (),
) -> str:
    """CONTEXT blocks, optional NOTES, then QUESTION (history is passed as prior turns, never here)."""
    context = "\n\n".join(context_block(i, b) for i, b in enumerate(blocks, start=1))
    text = f"CONTEXT:\n{context}\n\n"
    if notes:
        text += "NOTES:\n" + "\n".join(f"- {n}" for n in notes) + "\n\n"
    text += f"QUESTION: {question}"
    if interpreted_as and interpreted_as.strip().lower() != question.strip().lower():
        text += f"\n(Interpreted as: {interpreted_as})"
    return text


def build_messages(
    history: Sequence[ChatMessage],
    blocks: Sequence[Candidate],
    question: str,
    interpreted_as: str | None,
    max_history: int,
    notes: Sequence[str] = (),
) -> list[ChatMessage]:
    """Prior turns (last `max_history`) + the grounded user turn."""
    prior = list(history)[-max_history:] if max_history > 0 else []
    return [*prior, ChatMessage("user", user_turn(blocks, question, interpreted_as, notes))]
