"""End-to-end retrieval on the real corpus. Dense vectors come from the test-only FakeEmbedder (hashing),
so these tests verify the *pipeline mechanics* (fusion, weights, rerank, assembly, conflicts, caching);
retrieval *quality* with real OpenAI embeddings is measured by the eval harness."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.ingest.__main__ import conflict_groups
from app.ingest.build import build_index
from app.ingest.pipeline import Corpus
from app.retrieval.context import ConflictGroup
from app.retrieval.gate import GateConfig
from app.retrieval.pipeline import Retriever, preprocess
from app.retrieval.rerank import FlashRankReranker, NoneReranker
from app.retrieval.store import IndexStore
from app.settings import ROOT_DIR, Settings
from tests.fakes import FakeEmbedder

pytestmark = pytest.mark.corpus


@pytest.fixture(scope="module")
def store_and_settings(
    corpus: Corpus, tmp_path_factory: pytest.TempPathFactory
) -> tuple[IndexStore, Settings]:
    tmp = tmp_path_factory.mktemp("ret")
    settings = Settings(openai_api_key="sk-test", index_root=tmp / "idx", cache_dir=tmp / "cache")  # type: ignore[arg-type]
    build_index(
        corpus.chunks, settings, provider="openai", embedder=FakeEmbedder(), conflicts=conflict_groups(corpus)
    )
    return IndexStore.load(settings.index_dir, settings), settings


def _retriever(store: IndexStore, settings: Settings, reranker: object | None = None) -> Retriever:
    cache = ROOT_DIR / ".cache" / "flashrank"
    if reranker is None:
        reranker = (
            FlashRankReranker(settings.reranker_model, cache)
            if (cache / settings.reranker_model).exists()
            else NoneReranker()
        )
    conflicts = [ConflictGroup(**{k: v for k, v in g.items() if k != "detail"}) for g in store.conflicts]
    return Retriever(
        store, FakeEmbedder(), reranker, settings, GateConfig.from_dict(settings.load_thresholds()), conflicts
    )  # type: ignore[arg-type]


def test_conflicts_written_at_ingest(store_and_settings: tuple[IndexStore, Settings]) -> None:
    store, _ = store_and_settings
    members = {(g["doc_id"], tuple(g["members"])) for g in store.conflicts}
    assert ("personal_loans", ("21", "4.2")) in members
    assert ("credit_cards", ("FAQ:Q002", "1.2")) in members
    assert ("payments_upi", ("2", "21")) in members


def test_preprocess_lexical_copy() -> None:
    assert preprocess("FD rate on Rs 5 lakh") == "FD rate on ₹5 lakh fixed deposit"


@pytest.mark.parametrize("mode", ["dense", "bm25", "hybrid", "hybrid_rerank"])
async def test_modes_return_blocks_and_debug(
    store_and_settings: tuple[IndexStore, Settings], mode: str
) -> None:
    store, settings = store_and_settings
    result = await _retriever(store, settings).retrieve(
        "What is the foreclosure charge after 24 months on a personal loan?", mode
    )  # type: ignore[arg-type]
    assert result.blocks and len([b for b in result.blocks if b.reason == "retrieved"]) <= settings.final_k
    debug = result.debug()
    assert debug["mode"] == mode and debug["selected"] and debug["candidates"][0]["chunk_id"]
    if mode in ("bm25", "hybrid", "hybrid_rerank"):
        assert any(
            b.chunk.doc_id == "personal_loans" and b.chunk.section_id in ("6.2", "21") for b in result.blocks
        )


async def test_cross_document_question_retrieves_both_docs(
    store_and_settings: tuple[IndexStore, Settings],
) -> None:
    store, settings = store_and_settings
    result = await _retriever(store, settings).retrieve(
        "UPI daily limit in the payments SOP and UPI transfer limit for the savings account", "hybrid"
    )
    assert {"payments_upi", "savings_account"} <= {b.chunk.doc_id for b in result.blocks}
    assert {"payments_upi", "savings_account"} <= set(result.routed_docs)


async def test_mandate_fee_conflict_surfaces_both_values(
    store_and_settings: tuple[IndexStore, Settings],
) -> None:
    store, settings = store_and_settings
    result = await _retriever(store, settings).retrieve(
        "e-mandate registration e-sign charge for personal loan", "hybrid_rerank"
    )
    sections = {(b.chunk.doc_id, b.chunk.section_id) for b in result.blocks}
    assert {("personal_loans", "4.2"), ("personal_loans", "21")} <= sections
    text = " ".join(b.chunk.text for b in result.blocks)
    assert "₹150 to ₹400" in text and "₹150 - ₹350" in text


async def test_boilerplate_is_downweighted(store_and_settings: tuple[IndexStore, Settings]) -> None:
    store, settings = store_and_settings
    result = await _retriever(store, settings, NoneReranker()).retrieve("contactless tap limit", "hybrid")
    boiler = [c for c in result.ranked if c.chunk.boilerplate]
    assert boiler and all(
        c.weight <= settings.boilerplate_weight * settings.router_boost + 1e-9 for c in boiler
    )


async def test_results_are_cached(store_and_settings: tuple[IndexStore, Settings]) -> None:
    store, settings = store_and_settings
    retriever = _retriever(store, settings, NoneReranker())
    first = await retriever.retrieve("Video KYC timings", "hybrid")
    second = await retriever.retrieve("video   kyc TIMINGS", "hybrid")
    assert not first.cached and second.cached
    assert [b.chunk.chunk_id for b in first.blocks] == [b.chunk.chunk_id for b in second.blocks]
    assert retriever.results.hits == 1


async def test_reranker_failure_keeps_fused_order(
    store_and_settings: tuple[IndexStore, Settings], tmp_path: Path
) -> None:
    store, settings = store_and_settings

    class Broken:
        name = "broken"

        def score(self, query: str, passages: object) -> None:
            return None

    result = await _retriever(store, settings, Broken()).retrieve("Luxe annual fee waiver", "hybrid_rerank")
    assert result.blocks and all(c.rerank is None for c in result.ranked)
