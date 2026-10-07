"""Evaluation harness: golden set validity, metric definitions, calibration search."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.ingest.models import Chunk
from app.retrieval.gate import GateConfig
from eval.calibrate import search
from eval.golden import load_golden, resolve_gold_chunk_ids, validate_golden
from eval.metrics import (
    Gold,
    abstained,
    abstention_metrics,
    citation_metrics,
    forbidden_violations,
    key_fact_recall,
    percentile,
    retrieval_metrics,
)
from eval.run import evaluate_targets

ROOT = Path(__file__).resolve().parents[2]


def ch(
    doc: str,
    section: str,
    ctype: str = "policy",
    faq: str | None = None,
    dups: list[str] | None = None,
    text: str = "t",
) -> Chunk:
    return Chunk(
        chunk_id=f"{doc}-{section}-{faq}-{ctype}",
        doc_id=doc,
        doc_title="Doc",
        doc_code="C",
        effective_date="e",
        section_id=section,
        section_title="s",
        breadcrumb="b",
        page_start=1,
        page_end=1,
        chunk_type=ctype,  # type: ignore[arg-type]
        text=text,
        header="h",
        faq_id=faq,
        source_duplicates=dups or [],
    )


def test_golden_set_is_valid_and_covers_categories() -> None:
    items = load_golden()
    assert validate_golden(items) == []
    assert len(items) >= 70


def test_judge_prompt_copies_identical() -> None:
    for name in ("correctness", "groundedness"):
        assert (ROOT / "eval" / "judge_prompts" / f"{name}.txt").read_text(encoding="utf-8") == (
            ROOT / "prompts" / f"judge_{name}.txt"
        ).read_text(encoding="utf-8")


@pytest.mark.corpus
def test_every_gold_section_resolves_and_facts_are_in_gold_text(corpus: object) -> None:
    chunks = corpus.chunks  # type: ignore[attr-defined]
    for item in load_golden():
        ids = resolve_gold_chunk_ids(item, chunks)
        assert bool(ids) == bool(item["gold_sections"]), item["id"]
        if (
            item["answerable"]
            and item["expected_facts"]
            and item["category"] not in ("garbled", "unsupported", "adversarial")
        ):
            text = "\n".join(c.embed_text for c in chunks if c.chunk_id in set(ids))
            assert key_fact_recall(item["expected_facts"], text) == 1.0, item["id"]


def test_gold_matching_rules() -> None:
    assert Gold("d", "6").matches(ch("d", "6.2"))
    assert not Gold("d", "6.2").matches(ch("d", "6"))
    assert Gold("d", "FAQ:Q011").matches(ch("d", "23", "faq", "Q001", ["Q011"]))
    assert not Gold("d", "23").matches(ch("d", "23", "faq", "Q001"))
    assert Gold("d", "10").matches(ch("d", "7", "boilerplate", dups=["Section 10"]))
    assert not Gold("x", "6").matches(ch("d", "6"))


def test_retrieval_metrics_any_vs_all() -> None:
    ranked = [ch("d", "1"), ch("d", "6.2"), ch("d", "21"), ch("d", "6.2", "table_row")]
    any_item = {"gold_sections": [{"doc_id": "d", "section_id": "6.2"}, {"doc_id": "d", "section_id": "21"}]}
    m = retrieval_metrics(any_item, ranked, ranked[:2])
    assert m is not None
    assert m["hit@1"] == 0.0 and m["recall@3"] == 1.0 and m["mrr"] == 0.5 and m["context_precision"] == 0.5
    all_item = {**any_item, "gold_mode": "all"}
    m2 = retrieval_metrics(all_item, ranked, ranked)
    assert m2 is not None and m2["recall@1"] == 0.0 and m2["recall@3"] == 1.0
    assert retrieval_metrics({"gold_sections": []}, ranked, ranked) is None
    perfect = retrieval_metrics(any_item, [ch("d", "6.2")], [ch("d", "6.2")])
    assert perfect is not None and perfect["ndcg@10"] == 1.0


def test_key_facts_and_forbidden() -> None:
    answer = "The fee is Rs 1,00,000 per day, 1.50% markup, within T + 2 days; contact support."
    assert key_fact_recall(["₹1,00,000", "1.5%", "T+2", "support"], answer) == 1.0
    assert key_fact_recall(["5 lakh|500000", "markup"], answer) == 0.5
    assert key_fact_recall([], answer) is None
    assert forbidden_violations(["0%"], "charge is 10%") == []
    assert forbidden_violations(["0%"], "charge is 0%") == ["0%"]
    assert (
        forbidden_violations(["%"], "rate is 8.5%") == ["%"]
        and forbidden_violations(["%"], "not available") == []
    )
    assert forbidden_violations(["approved"], "Your loan is APPROVED") == ["approved"]


def test_abstention_detection_and_metrics() -> None:
    assert abstained({"answerable": False, "answer": "x"})
    assert abstained({"answerable": True, "answer": "The SOP does not specify the bounce fee amount."})
    assert not abstained({"answerable": True, "answer": "It is 3% [1]."})
    m = abstention_metrics([(True, True), (True, False), (False, False), (False, True)])
    assert m["precision"] == 0.5 and m["recall"] == 0.5 and m["over_refusal"] == 0.5


def test_citation_metrics() -> None:
    gold_chunk = ch("d", "6.2", text="foreclosure charge is 3%")
    other = ch("d", "4.2", text="processing fee")
    item = {"gold_sections": [{"doc_id": "d", "section_id": "6.2"}]}
    sources = [
        {
            "chunk_id": gold_chunk.chunk_id,
            "snippet": "foreclosure charge is 3%",
            "section_id": "6.2",
            "doc_title": "Doc",
            "page_start": 1,
            "citation": "Doc — Section 6.2 (p. 1)",
        },
        {
            "chunk_id": other.chunk_id,
            "snippet": "not in text",
            "section_id": "4.2",
            "doc_title": "Doc",
            "page_start": 1,
            "citation": "Doc — Section 4.2 (p. 1)",
        },
    ]
    m = citation_metrics(item, sources, {c.chunk_id: c for c in (gold_chunk, other)})
    assert m == {"snippet_exists": 0.5, "structural_match": 1.0, "precision": 0.5, "recall": 1.0}


def test_targets_and_percentile() -> None:
    targets = evaluate_targets(
        {"retrieval": {"recall@5": 0.95, "mrr": 0.7}, "robustness": {"injection_success_rate": 0.0}}
    )
    by_name = {t["metric"]: t for t in targets}
    assert by_name["Recall@5"]["met"] is True and by_name["MRR"]["met"] is False
    assert by_name["Injection success"]["met"] is True and by_name["Groundedness"]["met"] is None
    assert percentile([1, 2, 3, 4, 100], 50) == 3 and percentile([], 50) is None


def test_calibration_search_respects_over_refusal() -> None:
    def row(answerable: bool, level: float) -> dict[str, object]:
        return {
            "answerable": answerable,
            "features": {"rerank": level, "dense": level, "gap": 0.1, "lexical": level, "has_rerank": 1.0},
        }

    rows = (
        [row(True, 0.9) for _ in range(40)]
        + [row(True, 0.3)]
        + [row(False, 0.1) for _ in range(8)]
        + [row(False, 0.95)]
    )  # last one scores above every answerable
    base = GateConfig(
        {"rerank": 0.45, "dense": 0.25, "gap": 0.05, "lexical": 0.25}, 0.2, 0.65, 0.3, 0.7, 0.45
    )
    best = search(rows, base)  # type: ignore[arg-type]
    assert best["metrics"]["over_refusal"] <= 0.05
    assert best["metrics"]["recall"] == pytest.approx(8 / 9, abs=1e-3)


def test_transcript_renders_verbatim_output() -> None:
    from eval.transcript import render

    report = {
        "run_id": "r",
        "created_at": "t",
        "config": {"chat_model": "gpt-4.1-mini", "embed_model": "text-embedding-3-small"},
        "items": {
            "answers": [
                {
                    "id": "pl-01",
                    "category": "single_fact",
                    "question": "Q?",
                    "formatted": "Answer: A [1]\nSource: S",
                    "confidence": {"label": "High", "score": 0.9},
                    "abstained": False,
                    "verification": {"warnings": []},
                    "usage": {"latency_ms": {"total": 1}, "cost_usd": 0.0},
                }
            ]
        },
    }
    out = render(report)
    assert "## pl-01 · single_fact" in out and "Answer: A [1]\nSource: S" in out and "gpt-4.1-mini" in out
