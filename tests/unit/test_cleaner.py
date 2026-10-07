"""One test per extraction trap in PROMPT.md §3.2 (plus normalisation)."""

from __future__ import annotations

import pytest

from app.ingest.cleaner import (
    clean_cell,
    clean_lines,
    clean_text,
    deglue,
    fix_currency_glyph,
    normalize_unicode,
    strip_markdown,
    unwrap,
)
from app.ingest.models import Line
from app.ingest.structure import FAQ_ANYWHERE


# --- trap 1: currency glyph -----------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("fee of ■500 per bounce", "fee of ₹500 per bounce"),
        ("■1,00,000 per day", "₹1,00,000 per day"),
        ("cap ■(incl. GST)", "cap ₹(incl. GST)"),
        ("Charge: ■ 5,000", "Charge: ₹5,000"),
        ("decorative ■ bullet", "decorative  bullet"),
        ("■■", ""),
    ],
)
def test_currency_glyph_only_before_digits(raw: str, expected: str) -> None:
    assert fix_currency_glyph(raw) == expected


def test_currency_glyph_never_survives_cleaning() -> None:
    text = clean_text("Fee ■500 ■ and ■(x) and ■ 7\fnext ■")
    assert "■" not in text
    assert "₹500" in text and "₹(x)" in text and "₹7" in text


# --- trap 2: glued lines ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("glued", "expected_lines"),
    [
        ("Check status in 10 minsPAY-ERR-504 U88", ["Check status in 10 mins", "PAY-ERR-504 U88"]),
        ("Fee Beyond LimitSAV-OPS-501", ["Fee Beyond Limit", "SAV-OPS-501"]),
        (
            "Levies standard bounce feeSection 19: Payment Gateway",
            ["Levies standard bounce fee", "Section 19: Payment Gateway"],
        ),
        (
            "is the key.• Dispute turnaround time: 48 hours",
            ["is the key.", "• Dispute turnaround time: 48 hours"],
        ),
        (
            "[Reference Clause PAY-SOP-006].Q007: Can I cancel?",
            ["[Reference Clause PAY-SOP-006].", "Q007: Can I cancel?"],
        ),
        ("end of page one\fstart of page two", ["end of page one", "start of page two"]),
        (
            "paid in full by the due date.3.2 Minimum Amount Due",
            ["paid in full by the due date.", "3.2 Minimum Amount Due"],
        ),
    ],
)
def test_deglue_splits_glued_content(glued: str, expected_lines: list[str]) -> None:
    assert [p.strip() for p in deglue(glued).split("\n")] == expected_lines


def test_deglue_keeps_inline_ids_and_toc_entries() -> None:
    sentence = "operational compliance standard FB-OPS-007 for personal loan processing"
    assert deglue(sentence) == sentence
    toc = "• [Section 1: Policy Clause 1](#section-1)"
    assert deglue(toc) == toc


def test_faq_count_is_robust_to_gluing() -> None:
    glued = "".join(f"answer {i}.Q{i:03d}: question {i}?" for i in range(1, 101))
    assert len(FAQ_ANYWHERE.findall(glued)) == 100
    lines = clean_text(glued).split("\n")
    assert sum(1 for line in lines if line.startswith("Q") and ":" in line[:5]) == 100


# --- trap 3: soft-wrapped lines --------------------------------------------------------------------
def test_unwrap_rejoins_wrapped_question_counter() -> None:
    lines = [
        Line(
            "Q011: My UPI transaction failed but money was deducted. When will I receive a refund? (Payment incident case",
            9,
            wrapped=True,
            bold=True,
        ),
        Line("11)", 9, wrapped=False, bold=True),
        Line(
            "Under Section 2, failed UPI debits are automatically credited back within T+2 business days.",
            9,
            wrapped=False,
            bold=False,
        ),
    ]
    out = unwrap(lines)
    assert [line.text for line in out] == [
        "Q011: My UPI transaction failed but money was deducted. When will I receive a refund? (Payment incident case 11)",
        "Under Section 2, failed UPI debits are automatically credited back within T+2 business days.",
    ]


def test_unwrap_respects_font_weight_boundary() -> None:
    lines = [
        Line(
            "Q001: What is the foreclosure charge if I close my personal loan in 18 months? (Operational case 1)",
            3,
            wrapped=True,
            bold=True,
        ),
        Line(
            "According to FinBase Personal Loan Policy Section 4.2, because the loan is closed",
            3,
            wrapped=True,
            bold=False,
        ),
        Line("prior to completing 24 months.", 3, wrapped=False, bold=False),
    ]
    out = unwrap(lines)
    assert len(out) == 2
    assert out[1].text.endswith("is closed prior to completing 24 months.")


def test_unwrap_without_geometry_uses_punctuation() -> None:
    text = clean_text(
        "licensed Reserve Bank of\nIndia (RBI) partners.\n• next bullet\nSection 2: Heading\n2.1 Age and Nationality\nApplicants must be citizens."
    )
    assert text.split("\n") == [
        "licensed Reserve Bank of India (RBI) partners.",
        "• next bullet",
        "Section 2: Heading",
        "2.1 Age and Nationality",
        "Applicants must be citizens.",
    ]


def test_unwrap_never_joins_bullets_or_tables() -> None:
    lines = [
        Line("• first bullet without stop", 1, wrapped=True),
        Line("• second", 1),
        Line("⟦TABLE:x⟧", 1, table_id="x"),
        Line("after", 1),
    ]
    assert len(unwrap(lines)) == 4


# --- trap 4: markdown ------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("within **T+2 business days** (where", "within T+2 business days (where"),
        ("penalty of **1.00%** is deducted", "penalty of 1.00% is deducted"),
        ("call **1800-FIN-BASE (1800-346-2273)** now", "call 1800-FIN-BASE (1800-346-2273) now"),
        ("email `fraud-alert@finbase.com`.", "email fraud-alert@finbase.com."),
        ("dangling ** marker", "dangling  marker"),
    ],
)
def test_strip_markdown_keeps_words(raw: str, expected: str) -> None:
    assert strip_markdown(raw) == expected


def test_bold_spanning_wrapped_lines_is_removed() -> None:
    lines = [
        Line("an additional interest rate of **0.50%", 1, wrapped=True),
        Line("p.a.** across all deposit tenures.", 1),
    ]
    assert [line.text for line in clean_lines(lines)] == [
        "an additional interest rate of 0.50% p.a. across all deposit tenures."
    ]


# --- trap 5: table cells ------------------------------------------------------------------------
def test_multi_line_cells_are_merged() -> None:
    assert clean_cell("TLS 1.3 Perfect Forward\nSecrecy") == "TLS 1.3 Perfect Forward Secrecy"
    assert clean_cell("Executive Tier (Income >\n■1.5L/mo)") == "Executive Tier (Income > ₹1.5L/mo)"


# --- normalisation ---------------------------------------------------------------------------------
def test_normalize_unicode_nfc_and_spaces() -> None:
    decomposed = "Café  fee – − ok"
    assert normalize_unicode(decomposed) == "Café fee - - ok"


def test_truncated_values_are_not_repaired() -> None:
    assert "₹5,00,0" in clean_text("Contactless Tap ■5,00,0 ■25,000 Free")
    assert "₹1,00,0 + Interest" in clean_text("■1,00,0 + Interest accrued during cooling days")
