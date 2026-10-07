"""Indian-number / currency / duration normalisation shared by ingestion, BM25, routing and verification.

Design: we never *rewrite* source text with these helpers (the source must be quoted verbatim). They
produce canonical tokens used for lexical matching and figure verification:

    "₹1,00,000"  -> 100000          "1.2L" -> 120000         "50 Lakhs" -> 5000000
    "1.50%"      -> "1.5%"          "T + 2" -> "t+2"         "Rs. 500" -> 500
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

# Valid digit groupings: Indian (1,00,000 / 10,00,000) and international (100,000). Anything else is suspect.
_INDIAN_GROUPED = re.compile(r"^\d{1,2}(?:,\d{2})*,\d{3}$")
_INTL_GROUPED = re.compile(r"^\d{1,3}(?:,\d{3})+$")
_PLAIN = re.compile(r"^\d+$")

CURRENCY_PREFIX = r"(?:₹|Rs\.?|INR|■)\s?"
_NUM = r"\d[\d,]*(?:\.\d+)?"
_UNIT = r"(?:\s?(?:lakhs?|lacs?|crores?|cr|L|K)\b)?"

#: amount with an explicit currency marker, e.g. "₹1,00,000", "Rs 500", "₹1.5L", "INR 2 crore"
CURRENCY_RE = re.compile(rf"{CURRENCY_PREFIX}(?P<num>{_NUM})(?P<unit>{_UNIT})", re.IGNORECASE)
#: bare lakh/crore amounts without currency marker, e.g. "50 Lakhs", "5 lakh"
LAKH_RE = re.compile(rf"(?<![\w.])(?P<num>{_NUM})\s?(?P<unit>lakhs?|lacs?|crores?)\b", re.IGNORECASE)
PERCENT_RE = re.compile(r"(?<![\w.])(?P<num>\d+(?:\.\d+)?)\s?%")
TPLUS_RE = re.compile(r"\bT\s?\+\s?(?P<n>\d+)\b")
DURATION_RE = re.compile(
    r"(?<![\w.])(?P<num>\d+(?:\.\d+)?)\s?(?P<unit>(?:calendar |business |working |continuous |consecutive )?"
    r"(?:days?|months?|years?|yrs?|hours?|hrs?|minutes?|mins?|weeks?))\b",
    re.IGNORECASE,
)

_UNIT_MULTIPLIER = {
    "lakh": 100_000,
    "lakhs": 100_000,
    "lac": 100_000,
    "lacs": 100_000,
    "l": 100_000,
    "crore": 10_000_000,
    "crores": 10_000_000,
    "cr": 10_000_000,
    "k": 1_000,
}


def is_valid_grouping(num: str) -> bool:
    """True if the digit grouping of `num` (integer part) is a well-formed Indian/intl/plain number."""
    integer = num.split(".", maxsplit=1)[0]
    if "," not in integer:
        return bool(_PLAIN.match(integer))
    return bool(_INDIAN_GROUPED.match(integer) or _INTL_GROUPED.match(integer))


def parse_number(num: str, unit: str | None = None) -> Decimal | None:
    """Parse '1,00,000' / '1.2' + unit 'L' into a Decimal. Returns None for malformed grouping."""
    num = num.strip()
    if not is_valid_grouping(num):
        return None
    try:
        value = Decimal(num.replace(",", ""))
    except InvalidOperation:
        return None
    if unit:
        mult = _UNIT_MULTIPLIER.get(unit.strip().lower())
        if mult:
            value *= mult
    return value


def canonical_decimal(value: Decimal) -> str:
    """Canonical string for a number: no grouping, no trailing zeros ('1.50' -> '1.5', '100000.0' -> '100000')."""
    normalized = value.normalize()
    text = format(normalized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def canonical_amount(num: str, unit: str | None = None) -> str | None:
    """Canonical token for an amount, or None if the source value is malformed/truncated."""
    value = parse_number(num, unit)
    return None if value is None else canonical_decimal(value)


def lakh_form(value: Decimal) -> str | None:
    """'500000' -> '5 lakh' (only for exact multiples of 0.1 lakh >= 1 lakh)."""
    if value >= 10_000_000 and (value % 1_000_000) == 0:
        return f"{canonical_decimal(value / 10_000_000)} crore"
    if value >= 100_000 and (value % 10_000) == 0:
        return f"{canonical_decimal(value / 100_000)} lakh"
    return None


def find_malformed_amounts(text: str) -> list[str]:
    """Currency amounts whose digit grouping is invalid, e.g. '₹5,00,0' (truncated source values)."""
    bad: list[str] = []
    for match in CURRENCY_RE.finditer(text):
        num = match.group("num").rstrip(",.")
        if not is_valid_grouping(num):
            bad.append(match.group(0).strip())
    return bad


def numeric_tokens(text: str) -> list[str]:
    """Canonical lexical tokens for every amount / lakh form / percent / T+n in `text`.

    Used to enrich BM25 documents and queries so "5 lakh", "500000" and "₹5,00,000" all match.
    """
    tokens: list[str] = []
    for match in CURRENCY_RE.finditer(text):
        value = parse_number(match.group("num").rstrip(",."), match.group("unit") or None)
        if value is None:
            continue
        tokens.append(canonical_decimal(value))
        lf = lakh_form(value)
        if lf:
            tokens.extend(lf.split())
    for match in LAKH_RE.finditer(text):
        value = parse_number(match.group("num"), match.group("unit"))
        if value is not None:
            tokens.append(canonical_decimal(value))
    for match in PERCENT_RE.finditer(text):
        tokens.append(canonical_decimal(Decimal(match.group("num"))) + "%")
    for match in TPLUS_RE.finditer(text):
        tokens.append(f"t+{match.group('n')}")
    if re.search(r"\bp\.a\.|\bper annum\b|\bannual(?:ly)?\b", text, re.IGNORECASE):
        tokens.append("per_annum")
    return tokens


def normalize_query_numbers(text: str) -> str:
    """Normalise currency markers in a query (Rs/INR/₹) and spacing in T + n; meaning-preserving."""
    text = re.sub(r"\b(?:Rs\.?|INR)\s?(?=\d)", "₹", text, flags=re.IGNORECASE)
    text = TPLUS_RE.sub(lambda m: f"T+{m.group('n')}", text)
    return text
