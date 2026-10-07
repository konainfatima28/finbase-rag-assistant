"""Phase-2 canonical evidence & citation layer: identity, de-duplication, stable numbering, status, scope."""

from __future__ import annotations

from app.generation.canonical import build_evidence, evidence_key, evidence_label, related_items, renumber
from app.generation.evidence import ConflictEvidence, relevant_conflicts, relevant_unclear, words
from app.ingest.models import Chunk
from app.retrieval.context import Candidate, ConflictGroup

TITLES = {
    "personal_loans": "FinBase Personal Loans Master Policy & Operational Manual",
    "savings_account": "FinBase Digital Savings Accounts & Banking Operations Manual",
    "payments_upi": "FinBase Payments, UPI & Refund Settlement SOP",
    "kyc_security": "FinBase KYC Verification, Compliance & Security Master Document",
}


def ch(
    cid: str,
    text: str,
    *,
    doc: str = "personal_loans",
    section: str = "21",
    title: str = "Master Schedule of Personal Loan Fees and Charges",
    ctype: str = "table",
    parent: str | None = None,
    faq_id: str | None = None,
    page: int = 9,
) -> Chunk:
    return Chunk(
        chunk_id=cid,
        doc_id=doc,
        doc_title=TITLES[doc],
        doc_code="CODE",
        effective_date="2026-10-01",
        section_id=section,
        section_title=title,
        breadcrumb="b",
        page_start=page,
        page_end=page,
        chunk_type=ctype,  # type: ignore[arg-type]
        text=text,
        header=f"[{TITLES[doc]} | Section {section} {title} | p.{page}]",
        token_count=40,
        parent_chunk_id=parent,
        faq_id=faq_id,
    )


def blocks(*chunks: Chunk) -> list[Candidate]:
    return [Candidate(i, c) for i, c in enumerate(chunks)]


PROCESSING = "Fee Category: Application Processing Fee | Standard Charge: 1.5% of loan amount (Min ₹1,000, Max ₹15,000)"
MANDATE = "Fee Category: Mandate Registration / E-sign | Standard Charge: ₹150 - ₹350 (State Stamp Duty)"
CANCEL = "Fee Category: Loan Cancellation / Reversal Fee | Standard Charge: ₹1,00,0 + Interest accrued during cooling days"
T21 = ch("t21", f"{PROCESSING}\n{MANDATE}\n{CANCEL}")
ROW_PROCESSING = ch("r21a", f"Master Schedule — {PROCESSING}", ctype="table_row", parent="t21")
S42 = ch(
    "s42",
    "• Disbursal stamp duty and e-mandate registration fees (₹150 to ₹400 depending on state of execution) are borne by the borrower.",
    section="4.2",
    title="Upfront Processing Charges",
    ctype="policy",
    page=2,
)
S62 = ch(
    "s62",
    "• Foreclosure charge is 3% before 24 months.",
    section="6.2",
    title="Foreclosure Charges & Rules",
    ctype="policy",
    page=3,
)
FAQ_FULL = ch(
    "q8",
    "Q008: What is the maximum personal loan amount available at FinBase? (Operational case 8)\n"
    "The maximum loan amount is ₹15,00,000 (15 Lakhs) under Section 3.",
    section="23",
    title="Frequently Asked Questions",
    ctype="faq",
    faq_id="Q008",
    page=10,
)
FAQ_ORPHAN = ch(
    "q8q",
    "Q008: What is the maximum personal loan amount available at FinBase? (Operational case 8)",
    section="23",
    title="Frequently Asked Questions",
    ctype="faq",
    faq_id="Q008",
    page=10,
)
GROUP = ConflictGroup(
    "personal_loans",
    "range_conflict",
    ["21", "4.2"],
    "range starting ₹150 has different upper bounds ['350', '400']",
)


# --- identity and labels ---------------------------------------------------------------------------------
def test_logical_identity_and_human_labels_from_metadata() -> None:
    assert evidence_key(T21) == evidence_key(ROW_PROCESSING) == "personal_loans:section:21"
    assert evidence_key(FAQ_FULL) == evidence_key(FAQ_ORPHAN) == "personal_loans:faq:Q008"
    assert evidence_label(S42) == "Personal Loans — Section 4.2: Upfront Processing Charges"
    assert evidence_label(FAQ_FULL) == "Personal Loans — FAQ Q008"


# --- A. stable citation numbering -----------------------------------------------------------------------
def test_numbering_follows_first_citation_not_retrieval_order() -> None:
    ev = build_evidence("Foreclosure is 3% [3]. The processing fee is 1.5% [1].", blocks(T21, S42, S62))
    assert ev.answer == "Foreclosure is 3% [1]. The processing fee is 1.5% [2]."
    assert [(i.n, i.evidence_id) for i in ev.cited] == [
        (1, "personal_loans:section:6.2"),
        (2, "personal_loans:section:21"),
    ]


def test_numbering_is_stable_across_retrieval_orders_and_repeats() -> None:
    a = build_evidence("Foreclosure is 3% [3]. Fee is 1.5% [1].", blocks(T21, S42, S62))
    b = build_evidence(
        "Foreclosure is 3% [1]. Fee is 1.5% [3].", blocks(S62, S42, T21)
    )  # same answer, other order
    assert a.answer == b.answer
    assert [(i.n, i.evidence_id) for i in a.cited] == [(i.n, i.evidence_id) for i in b.cited]
    assert (
        build_evidence("Fee is 1.5% [1].", blocks(T21)).as_dict()
        == build_evidence("Fee is 1.5% [1].", blocks(T21)).as_dict()
    )


# --- B / D. de-duplication --------------------------------------------------------------------------------
def test_table_and_its_row_are_one_evidence_item_with_one_number() -> None:
    ev = build_evidence(
        "The processing fee is 1.5% [2][1]. It is non-refundable [2].", blocks(T21, ROW_PROCESSING)
    )
    assert ev.answer == "The processing fee is 1.5% [1]. It is non-refundable [1]."
    [item] = ev.items
    assert (
        item.n == 1 and item.chunk_ids == ["t21", "r21a"] and item.evidence_id == "personal_loans:section:21"
    )


def test_overlapping_chunks_of_one_section_collapse_but_other_sections_stay() -> None:
    part2 = ch(
        "s62b",
        "• Foreclosure is not permitted in the first 6 months.",
        section="6.2",
        title="Foreclosure Charges & Rules",
        ctype="policy",
        page=4,
    )
    ev = build_evidence("3% [1], not in 6 months [2]; fee 1.5% [3].", blocks(S62, part2, T21))
    assert [i.evidence_id for i in ev.items] == ["personal_loans:section:6.2", "personal_loans:section:21"]
    assert ev.items[0].chunk_ids == ["s62", "s62b"] and (ev.items[0].page_start, ev.items[0].page_end) == (
        3,
        4,
    )
    assert ev.answer == "3% [1], not in 6 months [1]; fee 1.5% [2]."


# --- C. duplicate FAQ retrieval -> one canonical FAQ item -----------------------------------------------
def test_duplicate_faq_retrievals_are_one_canonical_faq_item() -> None:
    ev = build_evidence("The maximum is ₹15,00,000 [2][1].", blocks(FAQ_ORPHAN, FAQ_FULL))
    [item] = ev.items
    assert item.n == 1 and item.source_type == "faq" and item.faq_id == "Q008"
    assert item.faq_question == "What is the maximum personal loan amount available at FinBase?"
    assert "Operational case" not in item.snippet and "₹15,00,000" in item.snippet
    assert item.chunk_id == "q8"  # the full entry (first cited) is the primary chunk, never the orphan


# --- E. unclear value only on the affected row ------------------------------------------------------------
def test_unclear_value_attaches_only_to_the_malformed_row() -> None:
    processing_q = words("What is the processing fee for a personal loan?")
    unclear = relevant_unclear([T21], processing_q, "The processing fee is 1.5% [1].")
    ev = build_evidence("The processing fee is 1.5% [1].", blocks(T21), unclear)
    assert ev.items[0].status == "normal" and ev.items[0].unclear_rows == [] and ev.unclear_values == []

    cancel_q = words("What is the loan cancellation fee?")
    unclear = relevant_unclear([T21], cancel_q, "It cannot be safely determined [1].")
    ev = build_evidence("It cannot be safely determined [1].", blocks(T21), unclear)
    [item] = ev.items
    assert item.status == "unclear_value" and [r["row"] for r in item.unclear_rows] == [
        "Loan Cancellation / Reversal Fee"
    ]
    assert (
        ev.unclear_values[0]["evidence_id"] == "personal_loans:section:21" and ev.unclear_values[0]["n"] == 1
    )
    assert "1,00,0" not in str(ev.as_dict()["unclear_values"])


def test_answer_quoting_the_malformed_value_marks_that_row() -> None:
    unclear = relevant_unclear([T21], set(), "", ["₹1,00,0"])
    ev = build_evidence("The fee is an incomplete value [1].", blocks(T21), unclear)
    assert ev.items[0].status == "unclear_value" and len(ev.items[0].unclear_rows) == 1


def test_uncited_unclear_evidence_is_kept_but_unnumbered() -> None:
    unclear = relevant_unclear([T21], words("loan cancellation fee"))
    ev = build_evidence("Foreclosure is 3% [2].", blocks(T21, S62), unclear)
    assert [(i.evidence_id, i.n, i.cited, i.status) for i in ev.items] == [
        ("personal_loans:section:6.2", 1, True, "normal"),
        ("personal_loans:section:21", None, False, "unclear_value"),
    ]
    assert [i.evidence_id for i in ev.cited] == ["personal_loans:section:6.2"]


# --- F / G. conflicts keep both sides; same-document labelling --------------------------------------------
def test_conflict_keeps_both_evidence_items_and_labels_same_document() -> None:
    bl = blocks(T21, S42)
    answer = "It is ₹150 - ₹350 [1] or ₹150 to ₹400 [2]."
    conflicts = relevant_conflicts(
        [GROUP], [T21, S42], words("What is the mandate fee for personal loans?"), answer, {1, 2}
    )
    ev = build_evidence(answer, bl, (), conflicts)
    assert [(i.label, i.status) for i in ev.items] == [
        (
            "Personal Loans — Section 21: Master Schedule of Personal Loan Fees and Charges",
            "conflicting_sources",
        ),
        ("Personal Loans — Section 4.2: Upfront Processing Charges", "conflicting_sources"),
    ]
    [record] = ev.conflicts
    assert record["scope"] == "same_document" and record["values"] == ["₹150 - ₹350", "₹150 to ₹400"]
    assert record["evidence_ids"] == ["personal_loans:section:21", "personal_loans:section:4.2"]
    assert record["citations"] == [1, 2] and all(record["conflict_id"] in i.conflict_ids for i in ev.items)
    assert record["description"].startswith("Section 21 and Section 4.2 of the FinBase Personal Loans")
    assert ev.statuses == ["conflicting_sources"]


# --- H. cross-document identity ---------------------------------------------------------------------------
def test_cross_document_evidence_keeps_document_identity() -> None:
    sav = ch(
        "sav3",
        "UPI Transfers: Daily cumulative limit of ₹1,00,000.",
        doc="savings_account",
        section="3",
        title="Electronic Fund Transfer Limits & Charges",
        ctype="policy",
    )
    upi = ch(
        "upi1",
        "UPI daily limit is ₹1,00,000.",
        doc="payments_upi",
        section="1",
        title="UPI Transaction Limits",
        ctype="policy",
    )
    ev = build_evidence("Both state ₹1,00,000 [1][2].", blocks(sav, upi))
    assert [(i.product, i.label) for i in ev.items] == [
        ("Savings Account", "Savings Account — Section 3: Electronic Fund Transfer Limits & Charges"),
        ("UPI Payments", "UPI Payments — Section 1: UPI Transaction Limits"),
    ]
    # a conflict whose sides are in different documents is labelled as such
    conflict = ConflictEvidence(
        ConflictGroup("x", "range_conflict", ["3", "1"]),
        {"3": 1, "1": 2},
        {},
        "x",
        ["Section 3", "Section 1"],
    )
    [record] = build_evidence("x [1][2].", blocks(sav, upi), (), [conflict]).conflicts
    assert record["scope"] == "cross_document" and not record["same_document"]


# --- I. product scope -------------------------------------------------------------------------------------
def test_other_product_evidence_is_marked_and_never_a_related_topic() -> None:
    bounce = ch("b21", "Fee Category: Mandate / Cheque Bounce Fee | Standard Charge: ₹500 per bounce")
    upi = ch(
        "u4",
        "U88 | Mandate Insufficient Balance | Levies standard bounce fee",
        doc="payments_upi",
        section="4",
        title="Response Codes",
        ctype="policy",
    )
    kyc = ch(
        "k1",
        "Full KYC removes limits.",
        doc="kyc_security",
        section="1",
        title="Customer Identification and KYC Tiers",
        ctype="policy",
    )
    q = "How much is the UPI AutoPay bounce fee?"
    ev = build_evidence("x [1]. y [2]. z [3].", blocks(bounce, upi, kyc), question=q)
    assert [i.scope for i in ev.items] == ["other_product", "in_scope", "general"]
    assert [r["evidence_id"] for r in related_items(blocks(bounce, upi, kyc), q)] == [
        "payments_upi:section:4",
        "kyc_security:section:1",
    ]
    assert all(
        i.scope == "unspecified"
        for i in build_evidence("x [1].", blocks(bounce), question="What is the bounce fee?").items
    )


# --- J. no percentages ------------------------------------------------------------------------------------
def test_public_items_carry_no_scores_or_percentages() -> None:
    item = build_evidence("x [1].", [Candidate(0, T21, rerank=0.97, rrf=0.03, final=0.97)]).items[0].as_dict()
    assert not {"relevance", "scores", "rerank", "rrf", "final", "score"} & set(item)
    related = related_items([Candidate(0, T21, rerank=0.97)], "fee")[0]
    assert not {"relevance", "scores"} & set(related) and related["n"] == 0


# --- M. claims map to real evidence ids ---------------------------------------------------------------------
def test_claims_point_to_real_evidence_ids() -> None:
    ev = build_evidence(
        "Foreclosure is 3% [3]. The fee is 1.5% [1][2]. Please contact support.",
        blocks(T21, ROW_PROCESSING, S62),
    )
    ids = {i.evidence_id for i in ev.items}
    assert ev.claims == [
        {"text": "Foreclosure is 3%.", "citations": [1], "evidence_ids": ["personal_loans:section:6.2"]},
        {"text": "The fee is 1.5%.", "citations": [2], "evidence_ids": ["personal_loans:section:21"]},
    ]
    assert all(set(c["evidence_ids"]) <= ids for c in ev.claims)


def test_renumber_drops_unknown_markers_and_keeps_order() -> None:
    assert renumber("a [3][1][3] b [9]", {1: 2, 3: 1}) == "a [1][2] b "
