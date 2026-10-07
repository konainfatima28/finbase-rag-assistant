"""Deterministic evidence status, decided in code rather than inferred from the LLM's wording.

Two statuses, deliberately distinct:
  * `unclear_value`       a row/line the question (or answer) is about holds a truncated or garbled amount,
                          e.g. 'Contactless Tap | Daily Limit: ₹5,00,0'. Status is attached per row/line, so
                          complete rows of the same table never inherit it.
  * `conflicting_sources` two authoritative passages give different values for the same item, e.g.
                          personal loans §4.2 '₹150 to ₹400' vs §21 '₹150 - ₹350' (same manual, two sections).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.generation.verifier import extract_figures
from app.ingest.models import Chunk
from app.retrieval.context import ConflictGroup, conflict_kind, display_text
from app.text.numbers import find_malformed_amounts

UNCLEAR_VALUE = "unclear_value"
CONFLICTING_SOURCES = "conflicting_sources"

_CODE = re.compile(r"^[A-Z]{2,}(?:-[A-Z0-9]+)+$")
_RANGE = re.compile(r"₹\s?[\d,]+(?:\.\d+)?\s*(?:-|–|to)\s*₹?\s?[\d,]+(?:\.\d+)?", re.I)
#: words that never identify a row/item on their own
_GENERIC = frozenset(
    [
        "free",
        "daily",
        "monthly",
        "limit",
        "limits",
        "charge",
        "charges",
        "amount",
        "value",
        "rupees",
        "standard",
        "extra",
        "fee",
        "fees",
        "loan",
        "loans",
        "personal",
        "account",
        "accounts",
        "savings",
        "card",
        "cards",
        "credit",
        "debit",
        "finbase",
        "bank",
        "within",
        "upto",
        "above",
        "below",
        "after",
        "before",
        "period",
        "days",
        "month",
        "months",
        "year",
        "years",
        "included",
        "include",
        "none",
        "applicable",
        "what",
        "which",
        "when",
        "where",
        "tell",
        "much",
        "many",
        "does",
        "have",
        "there",
        "that",
        "this",
        "with",
        "from",
        "your",
        "about",
        "available",
        "policy",
        "section",
    ]
)


# --------------------------------------------------------------------------- word matching
def words(text: str) -> set[str]:
    """Specific lower-case words (>= 4 letters, generic ones removed, light plural stemming)."""
    out = set()
    for word in re.findall(r"[a-z]{4,}", text.lower()):
        if word in _GENERIC:
            continue
        out.add(word[:-1] if word.endswith("s") and not word.endswith("ss") else word)
    return out


def overlaps(a: Iterable[str], b: Iterable[str]) -> bool:
    """Same word, or a shared 6-letter stem ('cancel' ~ 'cancellation', 'register' ~ 'registration')."""
    bs = set(b)
    for x in a:
        if x in bs:
            return True
        if len(x) >= 6 and any(len(y) >= 6 and x[:6] == y[:6] for y in bs):
            return True
    return False


# --------------------------------------------------------------------------- unclear values (per row)
@dataclass
class UnclearValue:
    """One truncated/garbled amount, located at row/line level."""

    chunk_id: str
    line: int
    raw: str
    column: str | None
    row: str
    label_words: frozenset[str]

    def describe(self) -> str:
        """'the Daily Limit for Contactless Tap' (never the garbled figure itself)."""
        if self.column and self.row:
            return f"the {self.column} for {self.row}"
        if self.row:
            return f"the amount for {self.row}"
        return "this amount"

    def as_dict(self) -> dict[str, Any]:
        """UI metadata; the garbled figure is intentionally not included."""
        return {"chunk_id": self.chunk_id, "line": self.line, "column": self.column, "row": self.row}


def _row_body(line: str) -> str:
    """Table-row chunks are prefixed with '<table title> — '."""
    return line.split(" — ", 1)[-1] if " — " in line and "|" in line else line


def unclear_values_in(chunk: Chunk) -> list[UnclearValue]:
    """Every malformed amount of a chunk with its column, row label and identifying words."""
    out: list[UnclearValue] = []
    lines = display_text(chunk).split("\n")
    for index, line in enumerate(lines):
        bad_values = find_malformed_amounts(line)
        if not bad_values:
            continue
        elsewhere = words(" ".join(lines[:index] + lines[index + 1 :]))
        for bad in bad_values:
            column: str | None = None
            row = ""
            if "|" in line:
                cells = [c.strip() for c in _row_body(line).split("|")]
                bad_cell = next((c for c in cells if bad in c), "")
                column = bad_cell.split(":", 1)[0].strip() if ":" in bad_cell else None
                others = [c.split(":", 1)[-1].strip() for c in cells if c != bad_cell]
                row = next(
                    (
                        v
                        for v in others
                        if re.search(r"[A-Za-z]{3}", v) and not _CODE.match(v) and not extract_figures(v)
                    ),
                    "",
                )
                label = " ".join(others)
            else:
                label = line.split(bad)[0]
                row = " ".join(label.strip(" •:-").split()[-6:])
            out.append(
                UnclearValue(chunk.chunk_id, index, bad, column, row, frozenset(words(label) - elsewhere))
            )
    return out


def relevant_unclear(
    chunks: Sequence[Chunk], question_words: set[str], answer: str = "", answer_quoted: Iterable[str] = ()
) -> list[UnclearValue]:
    """Unclear values the request is about: the row's identifying words match the question, or the answer
    talks about that row / quotes the garbled figure."""
    quoted = set(answer_quoted)
    answer_words = words(answer)
    out = []
    for chunk in chunks:
        for value in unclear_values_in(chunk):
            if (
                value.raw in quoted
                or (value.label_words and overlaps(value.label_words, question_words))
                or (value.label_words and overlaps(value.label_words, answer_words))
            ):
                out.append(value)
    return out


# --------------------------------------------------------------------------- conflicts
@dataclass
class ConflictEvidence:
    """A genuine conflict the request is about, with the values each side states."""

    group: ConflictGroup
    blocks: dict[str, int]  # member -> context block number
    values: dict[str, str]  # member -> value as written in that block ('' if none could be isolated)
    doc_title: str
    sections: list[str] = field(default_factory=list)

    @property
    def same_document(self) -> bool:
        """Conflict groups are detected within one document (two sections / FAQ vs body)."""
        return True

    def as_dict(self) -> dict[str, Any]:
        """JSON-friendly."""
        return {
            "status": CONFLICTING_SOURCES,
            "category": self.group.category,
            "doc_id": self.group.doc_id,
            "doc_title": self.doc_title,
            "same_document": self.same_document,
            "members": self.group.members,
            "sections": self.sections,
            "values": [self.values[m] for m in self.group.members if self.values.get(m)],
            "citations": [self.blocks[m] for m in self.group.members if m in self.blocks],
        }


def _anchors(group: ConflictGroup) -> tuple[set[str], list[str]]:
    amounts = {f.canonical for f in extract_figures(group.detail) if f.kind == "amount"}
    quoted = [q for q in re.findall(r"'([^']{3,})'", group.detail) if not q.isdigit()]
    return amounts, quoted


def _item_lines(chunk: Chunk, amounts: set[str], quoted: Sequence[str]) -> list[str]:
    """Lines of `chunk` that state the conflicted item (contain the conflict's amount or quoted wording)."""
    out = []
    for line in display_text(chunk).split("\n"):
        has_amount = bool(amounts & {f.canonical for f in extract_figures(line)})
        if has_amount or any(q.lower() in line.lower() for q in quoted):
            out.append(line)
    return out


def _value(lines: Sequence[str], amounts: set[str]) -> str:
    for line in lines:
        for match in _RANGE.finditer(line):
            if amounts & {f.canonical for f in extract_figures(match.group(0))}:
                return match.group(0).strip()
    return ""


def relevant_conflicts(
    groups: Sequence[ConflictGroup],
    block_chunks: Sequence[Chunk],
    question_words: set[str],
    answer: str,
    cited: set[int],
) -> list[ConflictEvidence]:
    """Genuine value conflicts the request is about.

    Decided in code: every side must be present in the context, and either the question names the
    conflicted item (its line's specific words) or the answer cites a side and states its amount.
    FAQ-vs-body and other non-amount conflicts additionally require the answer to cite every side.
    """
    answer_amounts = {f.canonical for f in extract_figures(answer) if f.kind == "amount"}
    out = []
    for group in groups:
        if conflict_kind(group.category) != "value_conflict":
            continue
        blocks: dict[str, int] = {}
        for n, chunk in enumerate(block_chunks, start=1):
            member = group.matches(chunk)
            if member is not None and member not in blocks:
                blocks[member] = n
        if len(blocks) < len(group.members) or len(group.members) < 2:
            continue
        amounts, quoted = _anchors(group)
        lines = {m: _item_lines(block_chunks[n - 1], amounts, quoted) for m, n in blocks.items()}
        asked = all(_is_best_line(block_chunks[n - 1], lines[m], question_words) for m, n in blocks.items())
        answered = bool(amounts & answer_amounts) and bool(cited & set(blocks.values()))
        if amounts:  # amount conflicts (e.g. ranges): decided by question/answer relevance
            if not (asked or answered):
                continue
        elif not (set(blocks.values()) <= cited and (asked or answered or _quotes_any(answer, quoted))):
            continue
        first = block_chunks[next(iter(blocks.values())) - 1]
        sections = [m if m.startswith("FAQ:") else f"Section {m}" for m in group.members if m in blocks]
        out.append(
            ConflictEvidence(
                group,
                blocks,
                {m: _value(lines[m], amounts) for m in blocks},
                first.doc_title,
                [s.replace("FAQ:", "FAQ ") for s in sections],
            )
        )
    return out


def overlap_count(a: Iterable[str], b: Iterable[str]) -> int:
    """How many words of `a` match a word of `b` (same word or shared 6-letter stem)."""
    return sum(1 for x in a if overlaps([x], b))


def _is_best_line(chunk: Chunk, item_lines: Sequence[str], question_words: set[str]) -> bool:
    """The question is about the conflicted item: in this section, the item's line matches the question at
    least as well as any other line (ties count). A shared generic word such as 'mandate' in 'mandate bounce
    fee' or 'disbursal' in a foreclosure question does not make the registration fee the topic."""
    if not item_lines:
        return False
    item_score = max(overlap_count(words(line), question_words) for line in item_lines)
    others = [line for line in display_text(chunk).split("\n") if line not in item_lines and line.strip()]
    best_other = max((overlap_count(words(line), question_words) for line in others), default=0)
    return item_score > 0 and item_score >= best_other


def _quotes_any(answer: str, quoted: Sequence[str]) -> bool:
    return any(q.lower() in answer.lower() for q in quoted)


def states_all_values(evidence: ConflictEvidence, answer: str, cited: set[int]) -> bool:
    """The answer already presents every side (each value's amounts appear and each side is cited)."""
    answer_amounts = {f.canonical for f in extract_figures(answer) if f.kind == "amount"}
    for member, n in evidence.blocks.items():
        value = evidence.values.get(member, "")
        needed = {f.canonical for f in extract_figures(value) if f.kind == "amount"}
        if n not in cited or (needed and not needed <= answer_amounts):
            return False
    return True


# --------------------------------------------------------------------------- product scope
#: explicit product names per document. KYC is cross-cutting (applies to every product), so it has none.
PRODUCT_TERMS: dict[str, re.Pattern[str]] = {
    "personal_loans": re.compile(r"\b(?:personal )?loans?\b|\bemis?\b", re.I),
    "credit_cards": re.compile(r"\bcredit cards?\b|\b(?:luxe|neo|metal) card\b", re.I),
    "payments_upi": re.compile(r"\bupi\b|\bautopay\b|\bimps\b|\bp2[pm]\b", re.I),
    "fd_wealth": re.compile(r"\bfds?\b|\bfixed deposits?\b|\bmutual funds?\b|\bsips?\b", re.I),
    "savings_account": re.compile(r"\bsavings\b|\bdebit cards?\b|\batms?\b", re.I),
}
_AMOUNT_QUESTION = re.compile(
    r"\bhow much\b|\bfees?\b|\bcharges?\b|\bcosts?\b|\bpenalt|\bkitna\b|\bkitni\b", re.I
)


def named_products(text: str) -> set[str]:
    """Documents whose product the text names explicitly ('UPI AutoPay' -> payments_upi)."""
    return {doc for doc, pattern in PRODUCT_TERMS.items() if pattern.search(text)}


def out_of_scope_amounts(question: str, answer: str, cited: Sequence[Chunk]) -> list[str]:
    """Amounts of the answer that ONLY a different product's document states, for a fee/charge question that
    names its product(s). Empty unless EVERY amount is such a transfer (e.g. a personal-loan EMI bounce fee
    given as the 'UPI AutoPay bounce fee'), so partially related answers are never affected."""
    named = named_products(question)
    if not named or not _AMOUNT_QUESTION.search(question):
        return []
    amounts = [f for f in extract_figures(answer) if f.kind == "amount"]
    if not amounts:
        return []
    transferred = []
    for fig in amounts:
        owners = {
            c.doc_id for c in cited if fig.canonical in {g.canonical for g in extract_figures(c.embed_text)}
        }
        if not owners or owners & named or not owners <= set(PRODUCT_TERMS):
            return []  # in scope, cross-cutting (KYC) or not from a cited block: not a product transfer
        transferred.append(fig.text)
    return transferred
