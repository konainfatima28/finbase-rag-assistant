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
from app.retrieval.context import Candidate, ConflictGroup, conflict_kind, display_text
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


#: an existence claim with FinBase as the explicit subject ("FinBase does not offer/provide/support X") --
#: deliberately NOT "have", which collides with ordinary feature-level sentences ("FinBase does not have a
#: minimum balance requirement"); deliberately requires the literal subject "FinBase", so a claim about one
#: specific product ("this loan does not have a processing fee") never matches at all.
_STOP = r"(?=[.,;:]|\s*\[|\s+(?:to|for|as|in|on|under|via|which|that|and)\b|$)"
_NEGATIVE_EXISTENCE = re.compile(
    r"\bFinBase\s+(?:currently\s+)?(?:does not|doesn't|do not|don't)\s+(?:currently\s+)?"
    r"(?:offer|provide|support)\s+([a-z][a-z /&-]{1,60}?)" + _STOP,
    re.I,
)
#: Deliberately no passive-voice mirror ("X is not available/offered"): that phrasing is also exactly how
#: the system is supposed to word a legitimate partial-answer disclosure (prompt rule 4, "state what is
#: not available in the knowledge base" -- e.g. "the international POS fee for the savings debit card is
#: not available in FinBase's documents"), so matching it on subject shape alone is not reliable enough to
#: tell a missing FACT from a missing PRODUCT/CATEGORY; the active form below, anchored to the literal
#: subject "FinBase", does not have that ambiguity.
#: the KIND of sentence that can actually ground an existence claim (an explicit negation/exclusion),
#: as opposed to a sentence that merely fails to mention the category
_EXCLUSION_WORDING = re.compile(
    r"\b(?:does|do)n't\b.{0,20}\b(?:offer|provide|support)\b|"
    r"\b(?:does|do)\s+not\b.{0,20}\b(?:offer|provide|support)\b|"
    r"\b(?:is|are)n't\b.{0,20}\b(?:offered|provided|supported|available)\b|"
    r"\b(?:is|are)\s+not\b.{0,20}\b(?:offered|provided|supported|available)\b|"
    r"\bnot\s+(?:offered|supported|available|provided)\b|\bunsupported\b|\bunavailable\b|"
    r"\bstrictly\s+(?:not|prohibited)\b|\bno longer\s+(?:offer|support)\b",
    re.I,
)
#: wrapper words that would otherwise trivially "match" almost any document (never the discriminating word)
_CLAIM_FILLER = frozenset(
    [
        "this",
        "that",
        "these",
        "those",
        "any",
        "such",
        "the",
        "and",
        "for",
        "with",
        "from",
        "your",
        "our",
        "their",
        "its",
        "services",
        "service",
        "products",
        "product",
        "options",
        "option",
        "finbase",
        "offer",
        "offers",
        "offering",
        "provide",
        "provides",
        "support",
        "supports",
        "currently",
    ]
)


def _claim_terms(phrase: str) -> set[str]:
    """Specific words of a claimed category/product name. No length-4 floor: 'car' must survive, unlike
    `words()` above which is tuned for table-row identification and drops short/product words on purpose."""
    return {w for w in re.findall(r"[a-z]{3,}", phrase.lower()) if w not in _CLAIM_FILLER}


def _chunk_covers(terms: set[str], text: str) -> bool:
    """Every term appears in `text` (exact token or shared 5-letter stem)."""
    tokens = set(re.findall(r"[a-z]{3,}", text.lower()))
    return bool(terms) and all(
        t in tokens or any(len(t) >= 5 and x[:5] == t[:5] for x in tokens) for t in terms
    )


def unsupported_negative_claims(answer: str, blocks: Sequence[Candidate]) -> list[str]:
    """Negative-existence claims ('FinBase does not offer X' / 'X is not offered') whose OWN cited block(s)
    never actually state that X is unavailable: an inference from a related product's silence, not a
    grounded fact (the model is never allowed to conclude absence merely because CONTEXT doesn't mention
    something). General by construction: the claimed category/product is read from the model's own
    sentence, never from a hardcoded product or category list, so this applies equally to every product.
    A genuine, explicitly-stated exclusion (e.g. the KB's own 'FinBase does NOT provide cryptocurrency
    trading' passage) passes, because that same wording is what `_EXCLUSION_WORDING` looks for in the
    cited block's own text."""
    chunks = [b.chunk for b in blocks]
    # Candidate support comes from every block cited ANYWHERE in the answer, not just the claim's own
    # sentence: a model routinely writes the claim and its citation as two sentences ("No, FinBase does
    # NOT offer X. This is explicitly stated in the policy [1]."). This is safe because a block is only
    # ever counted as support when its OWN text both names the claimed category/product (`_chunk_covers`,
    # every specific word of the claim) AND states an explicit exclusion (`_EXCLUSION_WORDING`) -- an
    # unrelated citation elsewhere in the answer essentially never satisfies both at once.
    all_numbers = {int(n) for n in re.findall(r"\[(\d{1,2})\]", answer) if 1 <= int(n) <= len(chunks)}
    cited = [chunks[n - 1] for n in all_numbers]
    unsupported: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", answer):
        match = _NEGATIVE_EXISTENCE.search(sentence)
        if not match:
            continue
        terms = _claim_terms(match.group(1))
        if not terms:
            continue
        supported = any(
            _chunk_covers(terms, c.embed_text) and _EXCLUSION_WORDING.search(c.embed_text) for c in cited
        )
        if not supported:
            unsupported.append(match.group(1).strip())
    return unsupported


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
