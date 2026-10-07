"""Lexical tokenisation for BM25 and lexical-overlap scoring, plus a deterministic token estimator."""

from __future__ import annotations

import math
import re

from app.text.numbers import numeric_tokens

_WORD_RE = re.compile(r"[a-z0-9]+(?:[.+][a-z0-9]+)*%?", re.IGNORECASE)
_PIECE_RE = re.compile(r"\w+|[^\w\s]")

STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "do",
        "does",
        "for",
        "from",
        "has",
        "have",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "me",
        "my",
        "of",
        "on",
        "or",
        "our",
        "so",
        "than",
        "that",
        "the",
        "their",
        "then",
        "there",
        "these",
        "this",
        "to",
        "under",
        "up",
        "was",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "you",
        "your",
        "yours",
        "am",
        "please",
        "tell",
        "about",
        "any",
        "per",
        "get",
    ]
)

#: Domain abbreviations expanded in the *lexical* copy of a query only (PROMPT.md §6.1).
ABBREVIATIONS: dict[str, str] = {
    "fd": "fixed deposit",
    "mad": "minimum amount due",
    "mab": "minimum average balance",
    "ovd": "officially valid documents",
    "v-kyc": "video kyc",
    "vkyc": "video kyc",
    "tds": "tax deducted at source",
    "apr": "annual percentage rate interest",
    "emi": "equated monthly instalment emi",
    "noc": "no-objection certificate",
    "dpd": "days past due",
    "foir": "fixed obligation to income ratio",
    "sip": "systematic investment plan",
    "p2p": "peer-to-peer",
    "p2m": "merchant",
    "tat": "turnaround time",
    "utr": "unique transaction reference",
    "kyc": "know your customer kyc",
    "2fa": "two-factor authentication",
    "gst": "gst tax",
}


def _light_stem(token: str) -> str:
    """Very light plural stemming (fees->fee, limits->limit); numbers untouched."""
    if token.isdigit() or len(token) <= 3:
        return token
    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "y"
    if token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def tokenize(text: str) -> list[str]:
    """Lower-case word tokens (stopwords removed, light stemming) + canonical numeric tokens."""
    words = [w.lower().rstrip(".") for w in _WORD_RE.findall(text)]
    tokens = [_light_stem(w) for w in words if w and w not in STOPWORDS]
    tokens.extend(numeric_tokens(text))
    return tokens


def expand_abbreviations(text: str) -> str:
    """Append expansions of known abbreviations (for the lexical query copy only)."""
    extra = [
        exp
        for abbr, exp in ABBREVIATIONS.items()
        if re.search(rf"(?<![\w-]){re.escape(abbr)}(?![\w-])", text, re.I)
    ]
    return text if not extra else f"{text} {' '.join(extra)}"


def count_tokens(text: str) -> int:
    """Deterministic, offline token estimate (~BPE-sized pieces).

    Counts word and punctuation pieces and scales by 1.1 for sub-word splits of numbers/codes. Measured
    on this corpus's chunks: estimate / cl100k_base ratio mean 0.98 (range 0.88-1.06); o200k_base mean
    0.99. Needs no network download (unlike tiktoken).
    """
    if not text:
        return 0
    return math.ceil(len(_PIECE_RE.findall(text)) * 1.1)
