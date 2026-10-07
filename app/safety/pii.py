"""PII redaction for user input and logs (PROMPT.md §8).

Masks: 16-digit card numbers (Luhn-valid or not, spaced/dashed), 12-digit Aadhaar, PAN, OTP/PIN/CVV/
password phrases, Indian phone numbers, personal e-mail addresses. Organisation contacts that appear in
the knowledge base (@finbase.com, 1800 toll-free) are not personal data and are left intact.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    ("card", re.compile(r"(?<!\d)(?:\d[ -]?){15}\d(?!\d)"), "[REDACTED_CARD]"),
    ("aadhaar", re.compile(r"(?<!\d)\d{4}[ -]?\d{4}[ -]?\d{4}(?!\d)"), "[REDACTED_AADHAAR]"),
    ("pan", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b", re.I), "[REDACTED_PAN]"),
    (
        "secret",
        re.compile(
            r"\b(otp|one[- ]time password|m?pin|cvv|cvc|password|passcode)(\s*(?:is|was|:|=|-)?\s*)([A-Za-z0-9@#$!]{3,12})\b",
            re.I,
        ),
        r"\1\2[REDACTED_SECRET]",
    ),
    ("phone", re.compile(r"(?<![\w-])(?:\+91[ -]?|0)?[6-9]\d{4}[ -]?\d{5}(?!\d)"), "[REDACTED_PHONE]"),
    (
        "email",
        re.compile(r"\b[A-Za-z0-9._%+-]+@(?!finbase\.com\b)[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
        "[REDACTED_EMAIL]",
    ),
]

_SECRET_WORDS = re.compile(r"\b(cvv|otp|pin|password|aadhaar|pan)\b", re.I)


@dataclass
class Redaction:
    """Redacted text plus which PII kinds were found."""

    text: str
    found: list[str] = field(default_factory=list)

    @property
    def had_pii(self) -> bool:
        """True if anything was masked."""
        return bool(self.found)


def redact(text: str) -> Redaction:
    """Mask PII in `text`. Idempotent (already-masked tokens are untouched)."""
    found: list[str] = []
    for kind, pattern, replacement in _PATTERNS:
        text, n = pattern.subn(replacement, text)
        if n:
            found.append(kind)
    return Redaction(text=text, found=found)


def mentions_sensitive_terms(text: str) -> bool:
    """True if the user mentions credentials (used to show a gentle 'don't share' notice)."""
    return bool(_SECRET_WORDS.search(text))
