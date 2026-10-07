"""Chunker + dedup invariants on the real corpus, and unit behaviour on synthetic input."""

from __future__ import annotations

import re
from collections import Counter

import pytest

from app.ingest.chunker import CHUNKER_VERSION, Chunker, ChunkerConfig, linearize_row, make_chunk_id
from app.ingest.dedup import assert_no_duplicates, mask_text
from app.ingest.models import DocMeta, Line, Table
from app.ingest.pipeline import Corpus, load_corpus
from app.ingest.structure import parse_structure
from tests.conftest import DATA_DIR

pytestmark = pytest.mark.corpus

REQUIRED = (
    "chunk_id",
    "doc_id",
    "doc_title",
    "doc_code",
    "effective_date",
    "section_id",
    "section_title",
    "breadcrumb",
    "header",
    "text",
)


def test_every_chunk_has_full_metadata(corpus: Corpus) -> None:
    for chunk in corpus.chunks:
        for name in REQUIRED:
            assert getattr(chunk, name), (chunk.chunk_id, name)
        assert chunk.page_start >= 1 and chunk.page_end >= chunk.page_start
        assert chunk.breadcrumb.startswith(chunk.doc_title)
        assert chunk.header.startswith(f"[{chunk.doc_title} | {chunk.doc_code} | Section {chunk.section_id}")
        assert chunk.token_count > 0
        if chunk.chunk_type == "faq":
            assert chunk.faq_id and chunk.question and chunk.clause_ref


def test_no_chunk_exceeds_max_tokens(corpus: Corpus) -> None:
    limit = ChunkerConfig().max_tokens
    assert max(c.token_count for c in corpus.chunks) <= limit


def test_chunks_are_clean(corpus: Corpus) -> None:
    for chunk in corpus.chunks:
        text = chunk.embed_text
        assert "■" not in text and "\f" not in text and "**" not in text and "`" not in text, chunk.chunk_id
        assert "](#section" not in text
        assert not any(line[:1].islower() for line in chunk.text.split("\n")), (
            chunk.chunk_id
        )  # no broken wraps


def test_exactly_100_faqs_before_dedupe_and_10_unique_after(corpus: Corpus) -> None:
    for cdoc in corpus.documents:
        assert len(cdoc.parsed.faqs) == 100
        assert len([c for c in cdoc.chunks if c.chunk_type == "faq"]) == 100
    after = Counter(c.doc_id for c in corpus.chunks if c.chunk_type == "faq")
    assert set(after.values()) == {10}


def test_dedup_leaves_no_identical_normalised_text(corpus: Corpus) -> None:
    assert_no_duplicates(corpus.chunks)
    assert len({c.chunk_id for c in corpus.chunks}) == len(corpus.chunks)


def test_boilerplate_collapsed_to_one_per_doc_and_downweightable(corpus: Corpus) -> None:
    boiler = [c for c in corpus.chunks if c.chunk_type == "boilerplate"]
    assert Counter(c.doc_id for c in boiler) == dict.fromkeys(corpus.dedup.report["by_doc"], 1)
    for chunk in boiler:
        assert chunk.boilerplate and len(chunk.source_duplicates) >= 13


def test_protocol_response_code_table_survives(corpus: Corpus) -> None:
    pay = next(c for c in corpus.chunks if c.doc_id == "payments_upi" and c.chunk_type == "boilerplate")
    for code in ("U16", "U30", "U69", "U88", "NPCI Timeout", "Levies standard bounce fee"):
        assert code in pay.text
    rows = [
        c
        for c in corpus.chunks
        if c.doc_id == "payments_upi" and c.chunk_type == "table_row" and "U69" in c.text
    ]
    assert rows and rows[0].parent_chunk_id == pay.chunk_id


def test_table_rows_point_to_existing_parent_and_are_intact(corpus: Corpus) -> None:
    ids = {c.chunk_id for c in corpus.chunks}
    rows = [c for c in corpus.chunks if c.chunk_type == "table_row"]
    assert rows
    for row in rows:
        assert row.parent_chunk_id in ids
        parent = next(c for c in corpus.chunks if c.chunk_id == row.parent_chunk_id)
        row_body = row.text.split(" — ", 1)[1]
        assert row_body in parent.text  # a row is never split across chunks


def test_foreclosure_chunk_is_section_6_2(corpus: Corpus) -> None:
    chunk = next(c for c in corpus.chunks if c.doc_id == "personal_loans" and c.section_id == "6.2")
    assert chunk.section_title == "Foreclosure Charges & Rules"
    assert "3% of the outstanding principal" in chunk.text and "1.5%" in chunk.text
    assert "initial 6 months" in chunk.text
    assert chunk.breadcrumb.endswith(
        "Section 6 Prepayment, Part-Payment, and Foreclosure Guidelines › 6.2 Foreclosure Charges & Rules"
    )


def test_fd_rate_table_keeps_intro_and_all_rows(corpus: Corpus) -> None:
    chunk = next(
        c
        for c in corpus.chunks
        if c.doc_id == "fd_wealth" and c.section_id == "1" and c.chunk_type == "table"
    )
    assert "minimum deposit of ₹1,000 up to ₹50,00,000" in chunk.text
    assert "2 years to 3 years" in chunk.text and "3 years to 5 years" in chunk.text
    assert "0.50% p.a." in chunk.text


def test_suspect_values_flagged(corpus: Corpus) -> None:
    flagged = {(c.doc_id, c.section_id) for c in corpus.chunks if c.quality_flag == "suspect_value"}
    assert ("personal_loans", "21") in flagged and ("savings_account", "4") in flagged
    for chunk in corpus.chunks:
        if "₹5,00,0 " in chunk.text or "₹1,00,0 " in chunk.text:
            assert chunk.quality_flag == "suspect_value"


def test_faq_wrong_section_reference_is_kept_as_text_only(corpus: Corpus) -> None:
    q1 = next(c for c in corpus.chunks if c.doc_id == "personal_loans" and c.faq_id == "Q001")
    assert q1.text_section_refs == ["4.2"]  # what the FAQ text says
    assert q1.section_id == "23"  # where it structurally is
    assert "FAQ Q001" in q1.citation_label and "Section 4.2" not in q1.citation_label


def test_ingestion_is_reproducible(corpus: Corpus) -> None:
    again = load_corpus(DATA_DIR)
    assert [c.chunk_id for c in again.chunks] == [c.chunk_id for c in corpus.chunks]
    assert [c.model_dump() for c in again.chunks] == [c.model_dump() for c in corpus.chunks]


# --- synthetic unit behaviour ---------------------------------------------------------------------
META = DocMeta(doc_id="d", title="Doc", code="C", effective_date="2026-10-01", file="d.pdf")


def test_linearize_row_repeats_header() -> None:
    assert (
        linearize_row(["Fee", "Amount", "Notes"], ["Late", "₹400 + GST", ""])
        == "Fee: Late | Amount: ₹400 + GST"
    )


def test_chunk_id_is_stable_and_content_sensitive() -> None:
    assert make_chunk_id("d", "1", "policy", "x") == make_chunk_id("d", "1", "policy", "x")
    assert make_chunk_id("d", "1", "policy", "x") != make_chunk_id("d", "1", "policy", "y")
    assert CHUNKER_VERSION.startswith("structure-")


def test_long_section_split_with_overlap_and_breadcrumb() -> None:
    bullets = [
        f"• Bullet number {i} explains a fairly long rule about fees, limits and timelines for customers."
        for i in range(60)
    ]
    lines = [Line("Section 1: Long", 1)] + [Line(b, 1 + i // 20) for i, b in enumerate(bullets)]
    doc = parse_structure(META, lines, {})
    chunks = Chunker(ChunkerConfig(max_tokens=300, target_tokens=200, overlap_ratio=0.15)).chunk(doc)
    body = [c for c in chunks if c.section_id == "1"]
    assert len(body) > 2
    assert all(c.token_count <= 300 for c in body)
    first, second = body[0].text.split("\n"), body[1].text.split("\n")
    assert first[-1] == second[0]  # overlap carries the last bullet
    assert all(re.match(r"^• Bullet number \d+ ", line) for c in body for line in c.text.split("\n"))
    assert {c.breadcrumb for c in body} == {"Doc › Section 1 Long"}


def test_large_table_gets_own_chunks_and_row_chunks() -> None:
    table = Table(
        "d-t001", 2, 3, ["Item", "Charge"], [[f"Item {i}", f"₹{i},000 + GST"] for i in range(1, 80)]
    )
    lines = [
        Line("Section 21: Charges", 2),
        Line("The schedule is:", 2),
        Line("⟦TABLE:d-t001⟧", 2, table_id="d-t001"),
    ]
    doc = parse_structure(META, lines, {"d-t001": table})
    chunks = Chunker(ChunkerConfig(max_tokens=300, target_tokens=200)).chunk(doc)
    tables = [c for c in chunks if c.chunk_type == "table"]
    rows = [c for c in chunks if c.chunk_type == "table_row"]
    assert len(tables) >= 2 and tables[0].text.startswith("The schedule is:")
    assert len(rows) == 79 and {r.parent_chunk_id for r in rows} == {tables[0].chunk_id}
    assert all(t.page_start == 2 and t.page_end == 3 for t in tables)


def test_mask_text_masks_counters_not_values() -> None:
    a = mask_text(
        "Q011: refund? (Payment incident case 11) Clause PAY-SOP-011 node 4 batch job PAY-REC-0011 ₹100 per day"
    )
    b = mask_text(
        "Q021: refund? (Payment incident case 21) Clause PAY-SOP-021 node 2 batch job PAY-REC-0021 ₹100 per day"
    )
    assert a == b
    assert mask_text("fee ₹100") != mask_text("fee ₹200")
