"""PII redaction (PROMPT.md §8)."""

from __future__ import annotations

import pytest

from app.safety.pii import mentions_sensitive_terms, redact


@pytest.mark.parametrize(
    ("text", "kind", "leaked"),
    [
        ("My card number is 4111 1111 1111 1111, is it blocked?", "card", "4111"),
        ("card 4111-1111-1111-1112 (not Luhn valid)", "card", "1112"),
        ("card 5500005555555559", "card", "5500005555555559"),
        ("Aadhaar 1234 5678 9012 please update", "aadhaar", "9012"),
        ("aadhaar 123456789012", "aadhaar", "123456789012"),
        ("PAN is ABCDE1234F", "pan", "ABCDE1234F"),
        ("my pan abcde1234f", "pan", "abcde1234f"),
        ("the otp is 482913, what now", "secret", "482913"),
        ("OTP: 1234", "secret", "1234"),
        ("my PIN 9876", "secret", "9876"),
        ("cvv=123", "secret", "123"),
        ("password is Hunter2!", "secret", "Hunter2"),
        ("call me on +91 98765 43210", "phone", "43210"),
        ("my number 9876543210", "phone", "9876543210"),
        ("mail me at ravi.k@gmail.com", "email", "ravi.k@gmail.com"),
    ],
)
def test_pii_is_masked(text: str, kind: str, leaked: str) -> None:
    out = redact(text)
    assert kind in out.found
    assert leaked not in out.text
    assert out.had_pii


@pytest.mark.parametrize(
    "safe",
    [
        "Foreclosure charge is 3% of the outstanding principal",
        "UPI limit is ₹1,00,000 per day and ₹5,00,000 IMPS",
        "Call 1800-FIN-BASE (1800-346-2273) or email fraud-alert@finbase.com",
        "Email support@finbase.com for help",
        "Within T+2 business days, 20 transactions per 24 hours",
        "Clause PAY-SOP-037 and batch job PAY-REC-0037",
    ],
)
def test_non_pii_left_intact(safe: str) -> None:
    out = redact(safe)
    assert out.text == safe and not out.had_pii


def test_redaction_is_idempotent() -> None:
    once = redact("card 4111 1111 1111 1111 otp is 1234").text
    assert redact(once).text == once


def test_sensitive_terms() -> None:
    assert mentions_sensitive_terms("Can you tell me my CVV?")
    assert not mentions_sensitive_terms("What is the foreclosure charge?")
