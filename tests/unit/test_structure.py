"""Section-tree parser and FAQ parser (PROMPT.md §3.2.8)."""

from __future__ import annotations

from app.ingest.cleaner import clean_lines
from app.ingest.models import DocMeta, Line, Table
from app.ingest.structure import parse_faqs, parse_structure

META = DocMeta(doc_id="doc", title="Test Doc", code="T-1", effective_date="2026-10-01", file="t.pdf")


def _lines(text: str, page: int = 1) -> list[Line]:
    return [Line(t, page) for t in text.split("\n")]


SAMPLE = """Test Doc
Document Code: T-1
Table of Contents
• [Section 1: Policy Clause 1](#section-1)
• [Section 2: Policy Clause 2](#section-2)
• [Section 3: Missing Section](#section-3)
• [Section 4: FAQ](#section-4)
Section 1: Intro and Tiers
Intro text.
1.1 FinBase Neo Credit Card (Lifetime Free)
• Fee: nil.
1.2 FinBase Luxe Credit Card
• Fee: ₹999.
Section 2: Standards 2
• Rule 2.1: something.
Section 6.2: Foreclosure Charges & Rules
• orphan explicit subsection without its parent heading.
Section 4: Comprehensive FAQ Directory (Items 1 - 2)
Q001: What is X? (Operational case 1)
Answer one under Section 4.2. [Reference Policy Section: Clause PL-REG-001].
• Applicability: tier PL-02.
Q002: What is Y? (Operational case 2)
Answer two. [Reference Policy Section: Clause PL-REG-002].
Section KYC-EXT-001: Extended Operational Protocol 1
• Audit retained 8 years."""


def test_toc_learnt_and_stripped() -> None:
    doc = parse_structure(META, clean_lines(_lines(SAMPLE)), {})
    assert doc.toc == {"1": "Policy Clause 1", "2": "Policy Clause 2", "3": "Missing Section", "4": "FAQ"}
    all_text = "\n".join(line.text for s in doc.sections.values() for line in s.lines)
    assert "](#section" not in all_text
    assert "3" not in doc.sections  # TOC-only section is not invented


def test_section_tree_and_breadcrumbs() -> None:
    doc = parse_structure(META, clean_lines(_lines(SAMPLE)), {})
    assert doc.sections["1.1"].parent_id == "1"
    assert doc.sections["1"].children == ["1.1", "1.2"]
    assert doc.sections["1.1"].title == "FinBase Neo Credit Card (Lifetime Free)"
    assert doc.breadcrumb("1.2") == "Test Doc › Section 1 Intro and Tiers › 1.2 FinBase Luxe Credit Card"
    # orphan explicit sub-section: parent "Section 6" is absent, so no parent is invented
    assert doc.sections["6.2"].parent_id is None and doc.sections["6.2"].level == 1
    assert doc.breadcrumb("6.2") == "Test Doc › Section 6.2 Foreclosure Charges & Rules"
    assert doc.sections["0"].kind == "front_matter"
    assert doc.sections["KYC-EXT-001"].kind == "annex"


def test_templated_heading_number_is_stripped() -> None:
    doc = parse_structure(META, clean_lines(_lines(SAMPLE)), {})
    assert doc.sections["2"].title == "Standards"
    assert doc.sections["2"].templated_title is True
    assert doc.sections["1"].templated_title is False


def test_faq_items_parsed_with_metadata() -> None:
    doc = parse_structure(META, clean_lines(_lines(SAMPLE)), {})
    assert [f.faq_id for f in doc.faqs] == ["Q001", "Q002"]
    q1 = doc.faqs[0]
    assert q1.question == "What is X? (Operational case 1)"
    assert q1.question_core == "What is X?"
    assert q1.clause_ref == "PL-REG-001"
    assert q1.text_section_refs == ["4.2"]
    assert q1.bullets == ["• Applicability: tier PL-02."]


def test_numeric_subheading_requires_matching_parent() -> None:
    lines = _lines("Section 3: Fees\n2.5 Lakh loans are common\n3.1 Real Sub")
    doc = parse_structure(META, clean_lines(lines), {})
    assert "3.1" in doc.sections and "2.5" not in doc.sections


def test_page_spans_tracked() -> None:
    lines = [
        Line("Section 1: A", 1),
        Line("text p1", 1),
        Line("text p2", 2),
        Line("Section 2: B", 3),
        Line("x", 3),
    ]
    doc = parse_structure(META, lines, {})
    assert (doc.sections["1"].page_start, doc.sections["1"].page_end) == (1, 2)
    assert (doc.sections["2"].page_start, doc.sections["2"].page_end) == (3, 3)


def test_tables_stay_in_section_order() -> None:
    table = Table("doc-t001", 1, 1, ["A", "B"], [["1", "2"]])
    lines = [
        Line("Section 1: A", 1),
        Line("intro:", 1),
        Line("⟦TABLE:doc-t001⟧", 1, table_id="doc-t001"),
        Line("after", 1),
    ]
    doc = parse_structure(META, lines, {"doc-t001": table})
    assert [line.table_id or line.text for line in doc.sections["1"].lines] == ["intro:", "doc-t001", "after"]


def test_parse_faqs_ignores_preamble() -> None:
    items = parse_faqs(_lines("intro line\nQ001: Q? (case 1)\nA."))
    assert len(items) == 1 and items[0].answer == "A."
