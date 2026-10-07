"""The corpus audit reproduces every PROMPT.md §3.3 finding from the PDFs (Phase-0 gate)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app import audit
from app.ingest.pipeline import Corpus
from tests.conftest import DATA_DIR

pytestmark = pytest.mark.corpus


@pytest.fixture(scope="module")
def result(corpus: Corpus) -> dict[str, object]:
    return audit.run_audit(DATA_DIR)


def test_gate_passes(result: dict) -> None:  # type: ignore[type-arg]
    assert result["gate"]["passed"], result["gate"]


def test_missing_sections(result: dict) -> None:  # type: ignore[type-arg]
    missing = {
        doc: [m["section"] for m in d["structure"]["missing_in_body"]]
        for doc, d in result["documents"].items()
    }
    assert missing == {
        "personal_loans": [],
        "credit_cards": ["22"],
        "savings_account": ["22"],
        "payments_upi": ["22"],
        "fd_wealth": ["21"],
        "kyc_security": ["21", "22"],
    }


def test_wrong_faq_citation_detected(result: dict) -> None:  # type: ignore[type-arg]
    issues = result["documents"]["personal_loans"]["faq_section_reference_issues"]
    q1 = next(i for i in issues if i["faq_id"] == "Q001")
    assert q1["cited_in_text"] == "4.2" and q1["supported_by"][0]["section"] == "6.2"
    for doc_id in ("credit_cards", "savings_account", "payments_upi", "fd_wealth", "kyc_security"):
        assert result["documents"][doc_id]["faq_section_reference_issues"] == []


def test_conflict_detectors(result: dict) -> None:  # type: ignore[type-arg]
    cats = {(f["category"], f["doc_id"]) for f in result["findings"]}
    assert ("range_conflict", "personal_loans") in cats
    assert ("faq_body_period_mismatch", "credit_cards") in cats
    assert ("faq_omits_condition", "savings_account") in cats
    assert ("faq_absolute_claim_vs_bounded_table", "savings_account") in cats
    assert ("scenario_dependent_sla", "payments_upi") in cats
    assert ("overlapping_buckets", "fd_wealth") in cats
    assert ("tax_wording_variance", "savings_account") in cats


def test_absence_probes(result: dict) -> None:  # type: ignore[type-arg]
    probes = result["absence_probes"]
    assert probes["home loan"] == [] and probes["UPI mandate bounce fee amount (payments SOP)"] == []
    assert all(
        hit.startswith("personal_loans")
        for hit in probes["bounce fee amount anywhere (shows where ₹500 belongs)"]
    )


def test_unsupported_services(result: dict) -> None:  # type: ignore[type-arg]
    names = [u["service"] for u in result["documents"]["fd_wealth"]["unsupported_services"]]
    assert len(names) == 4 and any("Chit Funds" in n for n in names)


def test_markdown_report_renders(result: dict, tmp_path: Path) -> None:  # type: ignore[type-arg]
    text = audit.render_markdown(result)
    assert "Phase-0 gate: PASS" in text and "NOT REPRODUCED" not in text
    out = tmp_path / "AUDIT.md"
    assert (
        audit.main(["--data-dir", str(DATA_DIR), "--out", str(out), "--json-out", str(tmp_path / "a.json")])
        == 0
    )
    assert out.read_text(encoding="utf-8").startswith("# FinBase Corpus Data Audit")
