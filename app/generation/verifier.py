"""Figure verifier (PROMPT.md §7.2.3): every amount / percentage / duration / T+n / count in the answer must
appear in the cited chunks (after normalisation), in the user's question, or be an explicitly computed value.

Normalisation: Indian grouping ('₹1,00,000' == '100000'), lakh/crore ('5 lakh'), '1.5%' == '1.50%',
'Rs'/'INR'/'₹', 'T + 2' == 'T+2'. A figure that "completes" a truncated source value (e.g. '₹1,00,000'
when the cited chunk says '₹1,00,0') is always unverified.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from app.ingest.models import Chunk
from app.text.numbers import (
    CURRENCY_RE,
    LAKH_RE,
    PERCENT_RE,
    TPLUS_RE,
    canonical_decimal,
    find_malformed_amounts,
    parse_number,
)

_STRIP = [
    re.compile(r"\[\d{1,2}\]"),  # citation markers
    re.compile(r"\bSection\s+[\w.-]+", re.I),  # section references are not figures
    re.compile(r"\bQ\d{3}\b"),
    re.compile(r"\b1800-[A-Z0-9-]+(?:\s*\(\d{4}-\d{3}-\d{4}\))?"),  # helpline (before generic codes)
    re.compile(r"\b[A-Z]{2,}(?:-[A-Z0-9]+)+\b"),  # codes like FB-POL-PL-2026-V4, PAY-ERR-403
    re.compile(r"\b20\d{2}\b"),  # years
    re.compile(r"\b\d{1,2}:\d{2}\s*(?:AM|PM)?", re.I),  # clock times handled separately
]
_K_SUFFIX = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s?k(?![a-z])", re.I)
_PLAIN = re.compile(r"(?<![\w.,₹])(\d+(?:\.\d+)?)(?![\w%])")
_TIME = re.compile(r"\b(\d{1,2}:\d{2})\s*(AM|PM)?", re.I)
_COMPUTED = re.compile(r"=\s*(?:₹|Rs\.?\s?|INR\s?)?([\d,]+(?:\.\d+)?)\s*(%)?")


@dataclass
class Figure:
    """One figure found in the answer."""

    text: str
    canonical: str
    kind: str
    status: str = "unverified"  # verified | computed | from_question | unverified


@dataclass
class Verification:
    """Verifier result."""

    figures: list[Figure] = field(default_factory=list)
    repaired_truncations: list[str] = field(default_factory=list)

    @property
    def unverified(self) -> list[str]:
        """Texts of figures that could not be verified."""
        return [f.text for f in self.figures if f.status == "unverified"]

    @property
    def rate(self) -> float:
        """Share of verified (incl. computed / from-question) figures; 1.0 when there are none."""
        if not self.figures:
            return 1.0
        return sum(1 for f in self.figures if f.status != "unverified") / len(self.figures)

    def as_dict(self) -> dict[str, Any]:
        """JSON-friendly."""
        return {
            "figures": [asdict(f) for f in self.figures],
            "unverified_figures": self.unverified,
            "repaired_truncations": self.repaired_truncations,
            "verified_rate": round(self.rate, 4),
        }


def _canon_number(raw: str) -> str | None:
    try:
        return canonical_decimal(Decimal(raw.replace(",", "")))
    except InvalidOperation:
        return None


def extract_figures(text: str) -> list[Figure]:
    """Figures in `text`, each with a canonical comparable form."""
    figures: list[Figure] = []
    work = text
    for match in CURRENCY_RE.finditer(work):
        num = match.group("num").rstrip(",.")
        value = parse_number(num, match.group("unit") or None)
        canonical = canonical_decimal(value) if value is not None else f"malformed:{num}"
        figures.append(Figure(match.group(0).strip().rstrip(",."), canonical, "amount"))
    work = CURRENCY_RE.sub(" ", work)
    for match in LAKH_RE.finditer(work):
        value = parse_number(match.group("num"), match.group("unit"))
        if value is not None:
            figures.append(Figure(match.group(0), canonical_decimal(value), "amount"))
    work = LAKH_RE.sub(" ", work)
    for match in PERCENT_RE.finditer(work):
        figures.append(
            Figure(match.group(0), canonical_decimal(Decimal(match.group("num"))) + "%", "percent")
        )
    work = PERCENT_RE.sub(" ", work)
    for match in TPLUS_RE.finditer(work):
        figures.append(Figure(match.group(0), f"t+{match.group('n')}", "t_plus"))
    work = TPLUS_RE.sub(" ", work)
    for match in _TIME.finditer(work):
        figures.append(Figure(match.group(0).strip(), match.group(1), "time"))
    for pattern in _STRIP:
        work = pattern.sub(" ", work)
    for match in _PLAIN.finditer(work):
        plain = _canon_number(match.group(1))
        if plain is not None:
            figures.append(Figure(match.group(1), plain, "number"))
    return figures


def canonical_pool(texts: Sequence[str]) -> set[str]:
    """Every canonical figure present in `texts` (amounts also contribute their plain number)."""
    pool: set[str] = set()
    for text in texts:
        for fig in extract_figures(text):
            pool.add(fig.canonical)
            if fig.kind == "percent":
                pool.add(fig.canonical.rstrip("%"))
            if fig.kind == "t_plus":
                pool.add(fig.canonical[2:])
        for match in re.finditer(r"\d[\d,]*(?:\.\d+)?", text):  # bare numbers inside codes/rows
            canonical = _canon_number(match.group(0))
            if canonical is not None:
                pool.add(canonical)
    return pool


def computed_values(answer: str) -> set[str]:
    """Results of explicit calculations ('… = ₹3,500') — accepted as computed, not sourced."""
    out: set[str] = set()
    for match in _COMPUTED.finditer(answer):
        canonical = _canon_number(match.group(1))
        if canonical is not None:
            out.add(canonical + ("%" if match.group(2) else ""))
    return out


def verify(
    answer: str, cited: Sequence[Chunk], question: str, *, trust_question: bool = True
) -> Verification:
    """Check every figure of `answer` against the cited chunks (+ question, + computed results)."""
    source_pool = canonical_pool([c.embed_text for c in cited])
    question_pool = canonical_pool([question]) if trust_question else set()
    if trust_question:  # informal amounts in the question: "20k" -> 20000
        question_pool |= {canonical_decimal(Decimal(m.group(1)) * 1000) for m in _K_SUFFIX.finditer(question)}
    computed = computed_values(answer)
    result = Verification()
    truncated = truncated_values([c for c in cited if c.quality_flag == "suspect_value"])
    for sentence in _sentences(answer):
        words = set(re.findall(r"[a-z]{4,}", sentence.lower()))
        for fig in extract_figures(sentence):
            quoted_as_is = fig.canonical.startswith("malformed:")  # verbatim garbled value: allowed
            if fig.kind == "amount" and not quoted_as_is and _repairs(fig.text, words, truncated):
                result.repaired_truncations.append(fig.text)
                fig.status = "unverified"
            elif fig.canonical in source_pool:
                fig.status = "verified"
            elif fig.canonical in computed:
                fig.status = "computed"
            elif fig.canonical in question_pool:
                fig.status = "from_question"
            result.figures.append(fig)
    return result


_LABEL_STOP = frozenset(
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
    ]
)


def truncated_values(chunks: Sequence[Chunk]) -> list[tuple[str, set[str]]]:
    """(digits of each malformed amount, label words of the row/line it sits in) for suspect chunks."""
    out: list[tuple[str, set[str]]] = []
    for chunk in chunks:
        lines = chunk.text.split("\n")
        for index, line in enumerate(lines):
            elsewhere = set(re.findall(r"[a-z]{4,}", " ".join(lines[:index] + lines[index + 1 :]).lower()))
            for bad in find_malformed_amounts(line):
                if "|" in line:  # table row "Col: value | Col: value": label = the other cells' values
                    cells = line.split(" — ")[-1].split("|")
                    label = " ".join(c.split(":", 1)[-1] for c in cells if bad not in c)
                else:  # prose: the text before the value
                    label = line.split(bad)[0]
                words = {
                    w
                    for w in re.findall(r"[a-z]{4,}", label.lower())
                    if w not in _LABEL_STOP and w not in elsewhere
                }
                out.append((re.sub(r"\D", "", bad), words))
    return out


def _repairs(text: str, sentence_words: set[str], truncated: list[tuple[str, set[str]]]) -> bool:
    """An amount 'repairs' a truncated value if it re-uses/extends its digits in a sentence about that row."""
    digits = re.sub(r"\D", "", text)
    return any(t and digits.startswith(t) and (not label or label & sentence_words) for t, label in truncated)


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [p for p in parts if p.strip()]
