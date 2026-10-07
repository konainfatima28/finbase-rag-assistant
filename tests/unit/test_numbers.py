"""Indian number / lakh / percent / T+n normalisation (PROMPT.md §3.2.6)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.text.numbers import (
    canonical_amount,
    find_malformed_amounts,
    is_valid_grouping,
    lakh_form,
    normalize_query_numbers,
    numeric_tokens,
    parse_number,
)
from app.text.tokenize import count_tokens, expand_abbreviations, tokenize


@pytest.mark.parametrize(
    ("num", "unit", "expected"),
    [
        ("1,00,000", None, "100000"),
        ("5,00,000", None, "500000"),
        ("50,00,000", None, "5000000"),
        ("100,000", None, "100000"),
        ("1.2", "L", "120000"),
        ("5", "L", "500000"),
        ("50", "Lakhs", "5000000"),
        ("2", "crore", "20000000"),
        ("1.50", None, "1.5"),
        ("999", None, "999"),
    ],
)
def test_canonical_amount(num: str, unit: str | None, expected: str) -> None:
    assert canonical_amount(num, unit) == expected


@pytest.mark.parametrize("bad", ["5,00,0", "1,00,0", "1,0000", "12,3"])
def test_malformed_grouping_is_rejected(bad: str) -> None:
    assert not is_valid_grouping(bad)
    assert parse_number(bad) is None


def test_find_malformed_amounts_flags_truncated_values_only() -> None:
    text = "Contactless Tap ₹5,00,0 monthly ₹25,000; fee ₹1,00,0 + interest; ok ₹1,00,000 and Rs 500"
    assert find_malformed_amounts(text) == ["₹5,00,0", "₹1,00,0"]


def test_lakh_form() -> None:
    assert lakh_form(Decimal(500000)) == "5 lakh"
    assert lakh_form(Decimal(120000)) == "1.2 lakh"
    assert lakh_form(Decimal(50000000)) == "5 crore"
    assert lakh_form(Decimal(999)) is None


def test_numeric_tokens_cover_all_spellings() -> None:
    tokens = set(numeric_tokens("₹1,00,000 per day, ₹1.2L spend, 50 Lakhs, 1.50% p.a., T + 2 days"))
    assert {"100000", "1", "lakh", "120000", "5000000", "1.5%", "t+2", "per_annum"} <= tokens


@pytest.mark.parametrize(
    ("query", "doc"),
    [
        ("5 lakh", "₹5,00,000"),
        ("500000", "₹5,00,000"),
        ("Rs 500000", "₹5,00,000"),
        ("1.5%", "1.50% markup"),
        ("T+2", "T + 2 Business Days"),
    ],
)
def test_query_and_document_spellings_share_bm25_tokens(query: str, doc: str) -> None:
    assert set(tokenize(normalize_query_numbers(query))) & set(tokenize(doc))


def test_normalize_query_numbers() -> None:
    assert normalize_query_numbers("Rs. 500 and INR 1,000 within T + 2") == "₹500 and ₹1,000 within T+2"


def test_expand_abbreviations_lexical_copy_only() -> None:
    expanded = expand_abbreviations("What is the MAD on my card and FD TDS?")
    assert (
        "minimum amount due" in expanded
        and "fixed deposit" in expanded
        and "tax deducted at source" in expanded
    )
    assert expand_abbreviations("plain question") == "plain question"


def test_count_tokens_is_deterministic_and_monotonic() -> None:
    assert count_tokens("") == 0
    short, long = (
        count_tokens("foreclosure charge"),
        count_tokens("foreclosure charge is 3% of the outstanding principal"),
    )
    assert 0 < short < long
    assert count_tokens("same text") == count_tokens("same text")
