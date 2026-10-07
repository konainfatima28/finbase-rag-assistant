"""Phase-1 regression tests (manual-test findings): evidence status, per-row quality, de-duplication,
FAQ resolution, related topics, internal wording, raw scores."""

from __future__ import annotations

from app.generation.answer import content_terms, covers, same_document_wording, scrub_internal
from app.generation.canonical import build_evidence
from app.generation.citations import best_snippet, validate
from app.generation.evidence import relevant_unclear, unclear_values_in, words
from app.generation.prompts import context_block, context_notes
from app.ingest.models import Chunk
from app.retrieval.context import (
    AssemblyConfig,
    Candidate,
    ConflictGroup,
    assemble,
    content_lines,
    display_text,
    is_orphan_faq,
    resolve_faq,
)


def ch(
    cid: str,
    text: str,
    *,
    doc: str = "personal_loans",
    section: str = "21",
    ctype: str = "table",
    flag: str = "ok",
    parent: str | None = None,
    faq_id: str | None = None,
    question: str | None = None,
    tokens: int = 50,
) -> Chunk:
    return Chunk(
        chunk_id=cid,
        doc_id=doc,
        doc_title="FinBase Personal Loans Master Policy & Operational Manual",
        doc_code="FB-POL-PL-2026-V4",
        effective_date="e",
        section_id=section,
        section_title="Master Schedule of Personal Loan Fees and Charges",
        breadcrumb="b",
        page_start=9,
        page_end=9,
        chunk_type=ctype,  # type: ignore[arg-type]
        text=text,
        header=f"[Loans | Section {section} | p.9]",
        quality_flag=flag,  # type: ignore[arg-type]
        token_count=tokens,
        parent_chunk_id=parent,
        faq_id=faq_id,
        question=question,
    )


PROCESSING_ROW = (
    "Fee Category: Application Processing Fee | Standard Charge: 1.5% of loan amount (Min ₹1,000, Max ₹15,000) | "
    "Tax Applicability: 18% GST Extra | Waiver Condition: Non-refundable"
)
CANCEL_ROW = (
    "Fee Category: Loan Cancellation / Reversal Fee | Standard Charge: ₹1,00,0 + Interest accrued during cooling "
    "days | Tax Applicability: 18% GST Extra | Waiver Condition: Within 3-day cooling period"
)
TABLE_21 = ch("t21", f"{PROCESSING_ROW}\n{CANCEL_ROW}", flag="suspect_value")
CONTACTLESS = ch(
    "sav4",
    "Standard Code: SAV-OPS-402 | Service Type: International POS | Daily Limit: ₹1,00,000 | Monthly Cap: ₹5,00,000\n"
    "Standard Code: SAV-OPS-403 | Service Type: Contactless Tap | Daily Limit: ₹5,00,0 | Monthly Cap: ₹25,000 | "
    "Fee Beyond Limit: Free",
    doc="savings_account",
    section="4",
    ctype="boilerplate",
    flag="suspect_value",
)


# --- 3. unclear values: located per row, never shown, distinct from conflicts ------------------
def test_unclear_value_is_located_at_row_level() -> None:
    [value] = unclear_values_in(CONTACTLESS)
    assert (value.column, value.row, value.line) == ("Daily Limit", "Contactless Tap", 1)
    assert value.describe() == "the Daily Limit for Contactless Tap"
    assert "5,00,0" not in str(value.as_dict())  # the garbled figure is never part of the metadata
    [cancel] = unclear_values_in(TABLE_21)
    assert cancel.row == "Loan Cancellation / Reversal Fee" and cancel.column == "Standard Charge"


def test_unclear_value_relevance_follows_the_question() -> None:
    assert relevant_unclear([CONTACTLESS], words("What is the contactless transaction limit?"))
    assert not relevant_unclear([CONTACTLESS], words("What is the international POS limit?"))
    assert relevant_unclear([TABLE_21], words("loan cancellation fee in the cooling period"))
    assert not relevant_unclear([TABLE_21], words("What is the processing fee for a personal loan?"))
    # the answer quoting the garbled value makes it relevant whatever the question
    assert relevant_unclear([CONTACTLESS], set(), "", ["₹5,00,0"])


def test_unclear_note_for_the_llm_names_the_row_not_the_value() -> None:
    notes = context_notes([Candidate(0, CONTACTLESS)], [], [], "contactless limit?")
    note = next(n for n in notes if "incomplete or garbled" in n)
    assert "the Daily Limit for Contactless Tap" in note and "5,00,0" not in note
    assert "cannot be safely determined" in note


# --- 5. per-row source quality: complete rows never inherit the warning ------------------------
def test_complete_row_of_a_table_is_not_flagged() -> None:
    blocks = [Candidate(0, TABLE_21, rerank=0.8)]
    report = validate("The processing fee is 1.5% of the loan amount [1].", 1)
    unclear = relevant_unclear([TABLE_21], words("What is the processing fee?"), report.text)
    [source] = build_evidence(report.text, blocks, unclear).cited
    assert source.quality_flag == "ok" and source.unclear_rows == [] and source.status == "normal"
    assert source.snippet.startswith("Fee Category: Application Processing Fee")


def test_incomplete_row_of_the_same_table_is_flagged() -> None:
    blocks = [Candidate(0, TABLE_21, rerank=0.8)]
    report = validate("The cancellation fee is unclear in the source [1].", 1)
    unclear = relevant_unclear([TABLE_21], words("What is the loan cancellation fee?"), report.text)
    [source] = build_evidence(report.text, blocks, unclear).cited
    assert source.quality_flag == "suspect_value" and source.status == "unclear_value"
    assert source.unclear_rows == [
        {"chunk_id": "t21", "line": 1, "column": "Standard Charge", "row": "Loan Cancellation / Reversal Fee"}
    ]


# --- 4. same-document conflict wording ---------------------------------------------------------
def test_same_document_conflict_is_not_called_different_documents() -> None:
    text = same_document_wording("The documents differ on the upper bound. Two different documents state it.")
    assert "documents differ" not in text and "different documents" not in text
    assert "these sections of the same document differ" in text


# --- 7. duplicate evidence removed before the LLM ----------------------------------------------
def test_row_and_table_never_both_in_context() -> None:
    row = ch("r1", f"Master Schedule — {PROCESSING_ROW}", ctype="table_row", parent="t21", tokens=20)
    other = ch("p42", "Processing fee is 1.5% of the sanctioned amount.", section="4.2", ctype="policy")
    cands = [Candidate(i, c, final=1 - i / 10) for i, c in enumerate([row, TABLE_21, other])]
    out = assemble(cands, AssemblyConfig(final_k=5), [TABLE_21, row, other])
    ids = [b.chunk.chunk_id for b in out.blocks]
    assert ids == ["t21", "p42"]  # the row resolved to its table once; the table itself is not repeated


def test_identical_content_is_deduplicated_but_distinct_content_kept() -> None:
    a = ch("a", "• Zero Minimum Balance: no MAB.", doc="savings_account", section="1", ctype="policy")
    b = ch("b", "Zero Minimum Balance:   no MAB.", doc="savings_account", section="10", ctype="annex")
    c = ch(
        "c", "Zero Minimum Balance: no MAB. Interest credited quarterly.", doc="savings_account", section="2"
    )
    assert content_lines(a) == content_lines(b)
    cands = [Candidate(i, x, final=1 - i / 10) for i, x in enumerate([a, b, c])]
    out = assemble(cands, AssemblyConfig(final_k=5), [a, b, c])
    assert [blk.chunk.chunk_id for blk in out.blocks] == ["a", "c"]


# --- 8. FAQ resolution / display ---------------------------------------------------------------
FAQ_FULL = ch(
    "faq8",
    "Q008: What is the maximum personal loan amount available at FinBase? (Operational case 8)\n"
    "The maximum loan amount is ₹15,00,000 (15 Lakhs) under Section 3, subject to credit underwriting.\n"
    "• Applicability: All retail borrowers under loan tier code PL-01.",
    section="23",
    ctype="faq",
    faq_id="Q008",
    question="What is the maximum personal loan amount available at FinBase?",
)
FAQ_ORPHAN = ch(
    "faq8q",
    "Q008: What is the maximum personal loan amount available at FinBase? (Operational case 8)",
    section="23",
    ctype="faq",
    faq_id="Q008",
    question="What is the maximum personal loan amount available at FinBase?",
)


def test_faq_display_and_snippet_include_the_answer_without_case_labels() -> None:
    assert "(Operational case 8)" not in display_text(FAQ_FULL)
    snippet = best_snippet(FAQ_FULL, "")
    assert snippet.startswith(
        "Q008: What is the maximum personal loan amount available at FinBase? The maximum"
    )
    assert "Operational case" not in snippet and "₹15,00,000" in snippet
    assert "Operational case" not in context_block(1, Candidate(0, FAQ_FULL))
    for label in ("(Card operational case 8)", "(Payment incident case 8)", "(Wealth product query 8)"):
        faq = FAQ_FULL.model_copy(update={"text": FAQ_FULL.text.replace("(Operational case 8)", label)})
        assert label not in display_text(faq)


def test_orphan_faq_question_resolves_to_the_full_entry() -> None:
    assert is_orphan_faq(FAQ_ORPHAN) and not is_orphan_faq(FAQ_FULL)
    assert resolve_faq(FAQ_ORPHAN, [FAQ_ORPHAN, FAQ_FULL]) is FAQ_FULL
    assert resolve_faq(FAQ_ORPHAN, [FAQ_ORPHAN]) is None  # never shown on its own
    out = assemble([Candidate(0, FAQ_ORPHAN, final=1.0)], AssemblyConfig(final_k=3), [FAQ_ORPHAN, FAQ_FULL])
    assert [(b.chunk.chunk_id, b.reason) for b in out.blocks] == [("faq8", "faq_resolved")]
    out = assemble([Candidate(0, FAQ_ORPHAN, final=1.0)], AssemblyConfig(final_k=3), [FAQ_ORPHAN])
    assert out.blocks == []


# --- 2. internal wording never reaches the customer --------------------------------------------
def test_internal_words_are_scrubbed() -> None:
    text = scrub_internal(
        "The provided context does not mention a home loan. Block [2] says 3% [2]. "
        "The CONTEXT and NOTES list no other rate. NOT_FOUND"
    )
    for word in ("context", "CONTEXT", "NOTES", "NOT_FOUND", "Block [2]"):
        assert word not in text
    assert "FinBase's documents" in text and "3% [2]" in text
    assert scrub_internal("in the context of a personal loan") == "in the context of a personal loan"


# --- 9. related topics need every content term ----------------------------------------------
def test_related_topic_must_cover_all_query_terms() -> None:
    loan_rate = ch(
        "r", "Personal loan interest rates are fixed for the tenure.", section="4.1", ctype="policy"
    )
    assert not covers(content_terms("What is FinBase's home loan interest rate?"), loan_rate)
    assert covers(content_terms("personal loan interest rate"), loan_rate)


# --- conflicts list never contains truncated values ---------------------------------------------
def test_truncated_value_group_is_not_a_conflict() -> None:
    trunc = ConflictGroup("savings_account", "row_inconsistent_or_truncated", ["4"], "daily '₹5,00,0'")
    out = assemble([Candidate(0, CONTACTLESS, final=1.0)], AssemblyConfig(), [CONTACTLESS], [trunc])
    assert out.conflicts == []


# --- live-run finding: safe answers must not trip the system-prompt leak guard ---------------------
def test_expected_answer_phrasing_is_not_a_prompt_leak() -> None:
    from pathlib import Path

    from app.safety.injection import leaks_system_prompt

    system = (Path(__file__).resolve().parents[2] / "prompts" / "answer_system.txt").read_text(
        encoding="utf-8"
    )
    for answer in (
        "The daily limit for contactless tap payments is unclear in the source, so the exact value cannot be "
        "safely determined from the source; the monthly cap is ₹25,000 [1].",
        "Section 21 states ₹150 - ₹350 [1] while Section 4.2 of the same document states ₹150 to ₹400 [2]; "
        "these sections differ, so please confirm with FinBase support.",
        "I couldn't find this in FinBase's documents.",
    ):
        assert not leaks_system_prompt(answer, system), answer


def test_snippet_prefers_the_row_with_the_answer_figure() -> None:
    table = ch(
        "t3",
        "Loan Slab Code: PL-CORE | Minimum Amount: ₹1,50,001 | Maximum Amount: ₹5,00,000 | Target Segment: Mid-income\n"
        "Loan Slab Code: PL-EXECUTIVE | Minimum Amount: ₹10,00,001 | Maximum Amount: ₹15,00,000 | Target: Executive",
        section="3",
    )
    snippet = best_snippet(table, "The maximum personal loan amount is ₹15,00,000 (15 Lakhs) [1].")
    assert "PL-EXECUTIVE" in snippet


def test_inline_sentinel_is_not_deleted_into_the_opposite_meaning() -> None:
    """Eval finding (ab-08): 'rates for 2027 are NOT_FOUND in the knowledge base' became 'are in the knowledge base'."""
    assert scrub_internal("The rates for 2027 are NOT_FOUND in the current knowledge base.") == (
        "The rates for 2027 are not available in the current knowledge base."
    )
    assert scrub_internal("It is 3% [1]. NOT_FOUND") == "It is 3% [1]."
    assert scrub_internal("It is 3% [1].\nNOT_FOUND") == "It is 3% [1]."


# --- eval findings ab-08 / ab-09: honest openings when the documents cannot answer ---------------
def test_future_year_note_only_for_years_after_the_documents() -> None:
    fd = ch("fd1", "7 days to 180 days: 5.50% p.a.", doc="fd_wealth", section="1", ctype="table")
    fd = fd.model_copy(update={"effective_date": "2026-10-01"})
    notes = context_notes([Candidate(0, fd)], [], [], "What will FinBase's fixed deposit rates be in 2027?")
    assert any("values for 2027 are not available" in n for n in notes)
    notes = context_notes([Candidate(0, fd)], [], [], "What is the FD rate for 2026?")
    assert not any("not available in the knowledge base; you may then" in n for n in notes)


def test_missing_section_note_is_strict_only_when_nothing_covers_it() -> None:
    missing = [
        {
            "doc_id": "p",
            "doc_title": "P",
            "section_id": "22",
            "title": "Dispute Arbitration and Chargeback Workflow",
        }
    ]
    dispute = ch(
        "d", "Customers can raise a dispute in the app; provisional credit follows.", doc="p", section="3"
    )
    q = "What is the chargeback arbitration workflow after provisional credit?"
    [note] = [n for n in context_notes([Candidate(0, dispute)], [], missing, q) if "Section 22" in n]
    assert note.count("Begin the reply by saying that this information is not available") == 1
    covered = ch("c", "Chargeback arbitration workflow: step 1 ...", doc="p", section="5")
    [note] = [n for n in context_notes([Candidate(0, covered)], [], missing, q) if "Section 22" in n]
    assert "Answer from the other CONTEXT blocks if they cover the question" in note


# --- ab-02: a fee asked for one product is never answered with another product's figure -----------
def test_out_of_scope_amounts() -> None:
    from app.generation.evidence import named_products, out_of_scope_amounts

    loans = ch("l21", "Fee Category: Mandate / Cheque Bounce Fee | Standard Charge: ₹500 per bounce")
    upi = ch(
        "u4",
        "U88 | Mandate Insufficient Balance | Levies standard bounce fee",
        doc="payments_upi",
        section="4",
    )
    q = "How much is the bounce fee when a UPI AutoPay mandate fails due to insufficient balance?"
    assert named_products(q) == {"payments_upi"}
    assert out_of_scope_amounts(q, "It is ₹500 plus 18% GST [1][2].", [loans, upi]) == ["₹500"]
    # the product's own question, a question naming no product, and a non-fee question are unaffected
    assert out_of_scope_amounts("What is the EMI bounce fee on my personal loan?", "₹500 [1].", [loans]) == []
    assert out_of_scope_amounts("What is the mandate bounce fee?", "₹500 [1].", [loans]) == []
    assert out_of_scope_amounts("Which documents do I need for UPI?", "₹500 [1].", [loans]) == []
    # one in-scope amount keeps the answer (only complete transfers abstain)
    upi_fee = ch("u2", "Compensation: ₹100 per day beyond T+1", doc="payments_upi", section="2")
    answer = "₹100 per day [2]; loans charge ₹500 [1]."
    assert out_of_scope_amounts("How much is the UPI fee?", answer, [loans, upi_fee]) == []
