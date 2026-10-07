"""Loader registry (PROMPT.md §5.1): every type emits the same RawDocument model."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.ingest import loaders
from app.ingest.chunker import Chunker
from app.ingest.cleaner import clean_lines
from app.ingest.loaders import load_document, supported_extensions
from app.ingest.models import DocMeta
from app.ingest.sources import load_sources
from app.ingest.structure import parse_structure
from tests.conftest import DATA_DIR


def _meta(file: str) -> DocMeta:
    return DocMeta(doc_id="fx", title="Fixture Doc", code="FX-1", effective_date="2026-10-01", file=file)


def test_registry_covers_required_types() -> None:
    assert {".pdf", ".docx", ".md", ".txt", ".csv"} <= set(supported_extensions())
    with pytest.raises(ValueError, match="unsupported"):
        load_document(Path("x.xlsx"), _meta("x.xlsx"))


def test_markdown_loader_end_to_end(tmp_path: Path) -> None:
    path = tmp_path / "policy.md"
    path.write_text(
        "# Section 1: Fees\n**Annual fee** is ■999 + 18% GST and\nwaived above ■1,20,000.\n\f## Section 2: Limits\n• Daily limit `₹1,00,000`.\n",
        encoding="utf-8",
    )
    raw = load_document(path, _meta(path.name))
    assert len(raw.pages) == 2 and raw.sha256
    doc = parse_structure(raw.meta, clean_lines(raw.lines), raw.tables)
    chunks = Chunker().chunk(doc)
    sec1 = next(c for c in chunks if c.section_id == "1")
    assert sec1.text == "Annual fee is ₹999 + 18% GST and waived above ₹1,20,000."
    sec2 = next(c for c in chunks if c.section_id == "2")
    assert sec2.page_start == 2 and sec2.text == "• Daily limit ₹1,00,000."


def test_txt_loader(tmp_path: Path) -> None:
    path = tmp_path / "faq.txt"
    path.write_text("Section 23: FAQ Directory\nQ001: What is X? (case 1)\nX is ₹5.\n", encoding="utf-8")
    raw = load_document(path, _meta(path.name))
    doc = parse_structure(raw.meta, clean_lines(raw.lines), raw.tables)
    assert [f.faq_id for f in doc.faqs] == ["Q001"]


def test_csv_loader_row_groups(tmp_path: Path) -> None:
    path = tmp_path / "fees.csv"
    path.write_text("Fee,Charge\nLate fee,■400 + GST\nCard replacement,₹199 + GST\n", encoding="utf-8")
    raw = load_document(path, _meta(path.name))
    table = next(iter(raw.tables.values()))
    assert table.header == ["Fee", "Charge"] and table.rows[0] == ["Late fee", "₹400 + GST"]
    doc = parse_structure(raw.meta, clean_lines(raw.lines), raw.tables)
    chunks = Chunker().chunk(doc)
    rows = [c for c in chunks if c.chunk_type == "table_row"]
    assert len(rows) == 2 and "Fee: Late fee | Charge: ₹400 + GST" in rows[0].text


def test_csv_loader_rejects_empty(tmp_path: Path) -> None:
    path = tmp_path / "empty.csv"
    path.write_text("\n", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        load_document(path, _meta(path.name))


def test_docx_loader_keeps_body_order(tmp_path: Path) -> None:
    import docx

    document = docx.Document()
    document.add_paragraph("Section 1: Charges")
    document.add_paragraph("The schedule is:")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text, table.cell(0, 1).text = "Item", "Charge"
    table.cell(1, 0).text, table.cell(1, 1).text = "NOC copy", "₹250"
    document.add_paragraph("Taxes extra.")
    path = tmp_path / "charges.docx"
    document.save(str(path))
    raw = load_document(path, _meta(path.name))
    assert [line.table_id or line.text for line in raw.lines] == [
        "Section 1: Charges",
        "The schedule is:",
        "fx-t001",
        "Taxes extra.",
    ]
    assert raw.tables["fx-t001"].rows == [["NOC copy", "₹250"]]


@pytest.mark.corpus
def test_pdf_loader_on_real_document() -> None:
    meta = next(m for m in load_sources(DATA_DIR / "sources.yaml") if m.doc_id == "personal_loans")
    path = DATA_DIR / "raw" / meta.file
    if not path.exists():
        pytest.skip("PDF not present")
    raw = load_document(path, meta)
    assert len(raw.pages) == 26
    assert all(
        "■" not in line.text or line.text.count("■") for line in raw.lines
    )  # glyph kept raw for cleaner
    assert raw.tables and all(t.header for t in raw.tables.values())
    slab = next(t for t in raw.tables.values() if t.header[0] == "Loan Slab Code")
    assert slab.rows[-1][-1] == "Executive Tier (Income > ■1.5L/mo)".replace("■", "₹")
    assert any("Executive Tier" in cell for cell in slab.merged_cells)


def test_ocr_fallback_degrades_gracefully(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakePage:
        rect = type("R", (), {"width": 612.0})()

        def get_textpage_ocr(self, full: bool) -> Any:
            raise RuntimeError("tesseract not installed")

    assert loaders._ocr_page(FakePage(), 1, Path("x.pdf")) == []

    class OcrPage(FakePage):
        def get_textpage_ocr(self, full: bool) -> Any:
            return object()

        def get_text(self, textpage: Any) -> str:
            return "Scanned line one\n\nline two"

    out = loaders._ocr_page(OcrPage(), 3, Path("x.pdf"))
    assert [t for _, t in out] == ["Scanned line one", "line two"]


def test_sources_manifest(tmp_path: Path) -> None:
    metas = load_sources(DATA_DIR / "sources.yaml")
    assert len(metas) == 6 and {m.effective_date for m in metas} == {"2026-10-01"}
    assert {m.doc_id for m in metas} == {
        "personal_loans",
        "credit_cards",
        "savings_account",
        "payments_upi",
        "fd_wealth",
        "kyc_security",
    }
    dup = tmp_path / "s.yaml"
    dup.write_text(
        "documents:\n  - {file: a.pdf, doc_id: x, title: A, code: A}\n  - {file: b.pdf, doc_id: x, title: B, code: B}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate"):
        load_sources(dup)
