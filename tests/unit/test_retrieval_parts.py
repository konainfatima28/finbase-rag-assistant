"""Router, RRF, TTL cache, context assembly, gate, reranker."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.cache.ttl_lru import TTLLRUCache
from app.ingest.models import Chunk
from app.retrieval import rerank as rerank_mod
from app.retrieval.context import AssemblyConfig, Candidate, ConflictGroup, assemble
from app.retrieval.fusion import apply_weights, ranked, rrf
from app.retrieval.gate import GateConfig, evaluate, label, lexical_overlap, score
from app.retrieval.rerank import FlashRankReranker, NoneReranker, make_reranker
from app.retrieval.router import route
from app.settings import ROOT_DIR, Settings


# --- router ------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("query", "doc"),
    [
        ("What is the foreclosure charge on my personal loan?", "personal_loans"),
        ("Luxe card lounge visits", "credit_cards"),
        ("UPI failed U69 refund", "payments_upi"),
        ("FD rate for senior citizens and TDS", "fd_wealth"),
        ("Video KYC timings", "kyc_security"),
        ("free ATM withdrawals on savings account", "savings_account"),
        ("Can I buy Bitcoin?", "fd_wealth"),
    ],
)
def test_router_maps_domains(query: str, doc: str) -> None:
    assert doc in route(query)


def test_router_is_soft_and_multi_label() -> None:
    cross = route("Compare the UPI daily limit with the savings account UPI limit")
    assert {"payments_upi", "savings_account"} <= set(cross)
    assert route("hello there") == []


# --- fusion -----------------------------------------------------------------------------------
def test_rrf_formula_and_order() -> None:
    fused = rrf({"dense": [(1, 0.9), (2, 0.8)], "bm25": [(2, 10.0), (3, 5.0)]}, k=60)
    assert fused[2] == pytest.approx(1 / 62 + 1 / 61)
    assert fused[1] == pytest.approx(1 / 61) and fused[3] == pytest.approx(1 / 62)
    assert ranked(fused)[0][0] == 2
    weighted = apply_weights(fused, {2: 0.4})  # (1/62 + 1/61) * 0.4 < 1/61
    assert ranked(weighted)[0][0] == 1


# --- cache ------------------------------------------------------------------------------------
def test_ttl_lru_cache_expiry_eviction_and_metrics() -> None:
    now = [0.0]
    cache: TTLLRUCache[int] = TTLLRUCache(max_items=2, ttl_s=10, clock=lambda: now[0])
    cache.set("a", 1)
    cache.set("b", 2)
    assert cache.get("a") == 1
    cache.set("c", 3)  # evicts LRU "b"
    assert cache.get("b") is None and len(cache) == 2
    now[0] = 11
    assert cache.get("a") is None  # expired
    assert cache.hits == 1 and cache.misses == 2 and cache.hit_rate == pytest.approx(1 / 3)
    cache.clear()
    assert len(cache) == 0


# --- context assembly -------------------------------------------------------------------------
def _chunk(
    cid: str, doc: str = "d", section: str = "1", ctype: str = "policy", tokens: int = 100, **kw: object
) -> Chunk:
    return Chunk(
        chunk_id=cid,
        doc_id=doc,
        doc_title="Doc",
        doc_code="C",
        effective_date="2026-10-01",
        section_id=section,
        section_title="T",
        breadcrumb="Doc › T",
        page_start=1,
        page_end=1,
        chunk_type=ctype,  # type: ignore[arg-type]
        text=f"text {cid}",
        header=f"[Doc | C | Section {section} T | p.1]",
        token_count=tokens,
        **kw,  # type: ignore[arg-type]
    )


def _cands(chunks: list[Chunk]) -> list[Candidate]:
    return [Candidate(idx=i, chunk=c, final=1.0 - i * 0.01) for i, c in enumerate(chunks)]


def test_assembly_respects_section_cap_budget_and_final_k() -> None:
    chunks = [
        _chunk("a", section="1"),
        _chunk("b", section="1"),
        _chunk("c", section="1"),
        _chunk("d", section="2", tokens=900),
        _chunk("e", section="3"),
        _chunk("f", section="4"),
    ]
    out = assemble(_cands(chunks), AssemblyConfig(final_k=4, max_per_section=2, token_budget=500), chunks)
    assert [b.chunk.chunk_id for b in out.blocks] == ["a", "b", "e", "f"]  # c: section cap, d: budget
    assert sum(b.chunk.token_count for b in out.blocks) <= 500


def test_table_row_is_replaced_by_its_table_no_duplicate() -> None:
    parent = _chunk("tbl", section="21", ctype="table")
    row = _chunk("row", section="21", ctype="table_row", parent_chunk_id="tbl", tokens=30)
    other = _chunk("x", section="5")
    out = assemble(_cands([row, other]), AssemblyConfig(final_k=2), [parent, row, other])
    assert [(b.chunk.chunk_id, b.reason) for b in out.blocks] == [
        ("tbl", "table_for_row"),
        ("x", "retrieved"),
    ]


def test_row_kept_when_its_table_does_not_fit() -> None:
    parent = _chunk("tbl", section="21", ctype="table", tokens=900)
    row = _chunk("row", section="21", ctype="table_row", parent_chunk_id="tbl", tokens=30)
    out = assemble(_cands([row]), AssemblyConfig(final_k=2, token_budget=500), [parent, row])
    assert [(b.chunk.chunk_id, b.reason) for b in out.blocks] == [("row", "retrieved")]


def test_conflict_partner_is_added_even_if_not_retrieved() -> None:
    body42 = _chunk("s42", doc="personal_loans", section="4.2")
    sched = _chunk("s21", doc="personal_loans", section="21", ctype="table")
    row21 = _chunk("r21", doc="personal_loans", section="21", ctype="table_row", parent_chunk_id="s21")
    group = ConflictGroup("personal_loans", "range_conflict", ["21", "4.2"])
    out = assemble(_cands([body42]), AssemblyConfig(final_k=1), [body42, row21, sched], [group])
    assert [(b.chunk.chunk_id, b.reason) for b in out.blocks] == [
        ("s42", "retrieved"),
        ("s21", "conflict_partner"),
    ]
    assert out.conflicts == [group]


def test_conflict_group_matches_faq_duplicates() -> None:
    faq = _chunk(
        "q2", doc="credit_cards", section="23", ctype="faq", faq_id="Q002", source_duplicates=["Q012"]
    )
    group = ConflictGroup("credit_cards", "faq_body_period_mismatch", ["FAQ:Q012", "1.2"])
    assert group.matches(faq) == "FAQ:Q012"
    assert group.matches(_chunk("z", doc="other", section="1.2")) is None


# --- gate -------------------------------------------------------------------------------------
GATE = GateConfig(
    weights={"rerank": 0.45, "dense": 0.25, "gap": 0.05, "lexical": 0.25},
    dense_lo=0.2,
    dense_hi=0.65,
    abstain_threshold=0.3,
    high=0.7,
    medium=0.45,
)


def test_gate_scores_and_labels() -> None:
    chunk = _chunk("a")
    strong = [Candidate(0, chunk, dense=0.7, rerank=0.95), Candidate(1, _chunk("b"), dense=0.4, rerank=0.1)]
    conf = evaluate("text a", strong, strong[:1], GATE)
    assert not conf.abstain and conf.score > 0.7 and label(conf.score, GATE) == "High"
    weak = [Candidate(0, chunk, dense=0.15, rerank=0.01)]
    assert evaluate("home loan rate", weak, weak, GATE).abstain
    assert evaluate("anything", [], [], GATE).abstain
    assert label(0.5, GATE) == "Medium" and label(0.1, GATE) == "Low"


def test_gate_without_reranker_redistributes_weight() -> None:
    feats = {"rerank": 0.0, "dense": 1.0, "gap": 0.0, "lexical": 1.0, "has_rerank": 0.0}
    assert score(feats, GATE) == pytest.approx((0.25 + 0.25) / (0.25 + 0.05 + 0.25))


def test_lexical_overlap_ignores_generic_terms() -> None:
    block = [Candidate(0, _chunk("a"))]
    block[0].chunk.text = "foreclosure charge after 24 months"
    assert lexical_overlap("foreclosure charge", block) == 1.0
    assert lexical_overlap("home loan", block) == 0.0


def test_thresholds_file_is_valid() -> None:
    config = GateConfig.from_dict(Settings().load_thresholds())
    assert 0 < config.abstain_threshold < 1 and config.medium < config.high


# --- reranker ---------------------------------------------------------------------------------
def test_none_reranker_and_factory(tmp_path: Path) -> None:
    assert NoneReranker().score("q", ["p"]) is None
    assert make_reranker("none", "m", tmp_path).name == "none"
    assert isinstance(make_reranker("flashrank", "m", tmp_path), FlashRankReranker)


def test_flashrank_failure_falls_back(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import flashrank

    def boom(**kwargs: object) -> None:
        raise RuntimeError("model download failed")

    monkeypatch.setattr(flashrank, "Ranker", boom)
    reranker = FlashRankReranker("ms-marco-MiniLM-L-12-v2", tmp_path)
    assert reranker.score("q", ["a", "b"]) is None and not reranker.available
    assert reranker.load() is False  # failure is remembered, no retry storm
    assert rerank_mod.NoneReranker().score("q", []) is None


def test_flashrank_real_model_from_local_cache() -> None:
    cache = ROOT_DIR / ".cache" / "flashrank"
    if not (cache / "ms-marco-MiniLM-L-12-v2").exists():
        pytest.skip("FlashRank model not cached locally (downloaded on first real run)")
    reranker = FlashRankReranker("ms-marco-MiniLM-L-12-v2", cache)
    scores = reranker.score(
        "foreclosure charge after 24 months",
        [
            "Physical debit card annual fee is 199 rupees.",
            "If the loan is foreclosed after completing 24 months, the foreclosure charge is 1.5%.",
        ],
    )
    assert scores is not None and scores[1] > 0.5 > scores[0]


class _SortingFakeRanker:
    """Mimics flashrank.Ranker.rerank: results come back SORTED by score, not in input order."""

    def __init__(self) -> None:
        self.batch_sizes: list[int] = []

    def rerank(self, request: object) -> list[dict[str, object]]:
        passages = request.passages  # type: ignore[attr-defined]
        self.batch_sizes.append(len(passages))
        scored = [
            {"id": p["id"], "text": p["text"], "score": float(p["text"].split(":")[1]) / 100}
            for p in passages
        ]
        return sorted(scored, key=lambda r: -float(r["score"]))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("batch_size", "expected_batches"), [(4, [4, 4, 2]), (3, [3, 3, 3, 1]), (12, [10]), (1, [1] * 10)]
)
def test_batched_rerank_scores_align_with_original_passages(
    tmp_path: Path, batch_size: int, expected_batches: list[int]
) -> None:
    """D-034: candidates are scored in batches; every score must land on its own passage, in input order."""
    reranker = FlashRankReranker("m", tmp_path, batch_size=batch_size)
    fake = _SortingFakeRanker()
    reranker._ranker = fake  # loaded model stand-in (no download)
    values = [37, 5, 91, 12, 64, 3, 88, 49, 20, 76]  # deliberately unsorted
    passages = [f"p{i}:{v}" for i, v in enumerate(values)]
    scores = reranker.score("q", passages)
    assert scores == [v / 100 for v in values]
    assert fake.batch_sizes == expected_batches


def test_rerank_batch_size_setting_reaches_the_reranker(tmp_path: Path) -> None:
    assert Settings().rerank_batch_size == 4
    reranker = make_reranker("flashrank", "m", tmp_path, batch_size=4)
    assert isinstance(reranker, FlashRankReranker) and reranker.batch_size == 4
    assert FlashRankReranker("m", tmp_path, batch_size=0).batch_size == 1  # never a zero-size batch
