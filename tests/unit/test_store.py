"""Index build/load, manifest validation (fail fast on embedder mismatch), FAISS<->chunk mapping, BM25."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from app.ingest import __main__ as ingest_cli
from app.ingest.build import build_index, vector_inputs
from app.ingest.embed_cache import EmbeddingCache
from app.ingest.pipeline import Corpus
from app.retrieval.bm25 import BM25
from app.retrieval.store import (
    IndexManifest,
    IndexMismatchError,
    IndexNotFoundError,
    IndexStore,
    validate_manifest,
)
from app.settings import ROOT_DIR, Settings
from tests.fakes import FakeEmbedder


def _settings(tmp_path: Path, **kw: object) -> Settings:
    return Settings(
        openai_api_key="sk-test",  # type: ignore[arg-type]
        index_root=tmp_path / "indexes",
        cache_dir=tmp_path / "cache",
        data_dir=ROOT_DIR / "data",
        **kw,  # type: ignore[arg-type]
    )


@pytest.fixture(scope="module")
def built(
    corpus: Corpus, tmp_path_factory: pytest.TempPathFactory
) -> tuple[Settings, dict[str, object], FakeEmbedder]:
    tmp = tmp_path_factory.mktemp("idx")
    settings = _settings(tmp)
    embedder = FakeEmbedder()
    summary = build_index(corpus.chunks, settings, provider="openai", embedder=embedder)
    return settings, summary, embedder


pytestmark = pytest.mark.corpus


def test_index_files_and_manifest(
    built: tuple[Settings, dict[str, object], FakeEmbedder], corpus: Corpus
) -> None:
    settings, summary, _ = built
    directory = settings.index_dir
    assert directory.name == "openai-text-embedding-3-small"
    assert {p.name for p in directory.iterdir()} == {
        "manifest.json",
        "missing_sections.json",
        "conflicts.json",
        "chunks.jsonl",
        "index.faiss",
        "vectors_owner.json",
        "bm25.json",
    }
    manifest = IndexManifest.model_validate_json((directory / "manifest.json").read_text(encoding="utf-8"))
    assert manifest.embedder_provider == "openai" and manifest.embedder_model == "text-embedding-3-small"
    assert manifest.dim == 1536 and manifest.normalized and manifest.n_chunks == len(corpus.chunks)
    assert manifest.n_vectors == len(corpus.chunks) + 60  # + one question-only vector per unique FAQ
    assert set(manifest.source_pdf_sha256s) == {
        "personal_loans",
        "credit_cards",
        "savings_account",
        "payments_upi",
        "fd_wealth",
        "kyc_security",
    }
    assert (
        manifest.chunker_version.startswith("structure-")
        and manifest.content_hash == manifest.compute_content_hash()
    )
    assert summary["vectors"] == manifest.n_vectors


def test_faiss_ids_map_to_chunks_and_bm25_shares_order(
    built: tuple[Settings, dict[str, object], FakeEmbedder], corpus: Corpus
) -> None:
    settings, _, _ = built
    store = IndexStore.load(settings.index_dir, settings)
    assert [c.chunk_id for c in store.chunks] == [c.chunk_id for c in corpus.chunks]
    assert len(store.bm25) == len(store.chunks)
    _texts, owners = vector_inputs(store.chunks)
    assert list(store.owners) == owners
    faq_rows = [i for i, o in enumerate(owners) if i >= len(store.chunks)]
    assert all(store.chunks[owners[i]].chunk_type == "faq" for i in faq_rows)
    # Every chunk retrieves itself first through its own vector.
    embedder = FakeEmbedder()
    import asyncio

    for idx in (0, 37, len(store.chunks) - 1):
        vec = asyncio.run(embedder.embed([store.chunks[idx].embed_text]))
        assert store.dense_search(vec, 1)[0][0] == idx


def test_dense_search_collapses_faq_rows_to_one_chunk(
    built: tuple[Settings, dict[str, object], FakeEmbedder],
) -> None:
    settings, _, _ = built
    store = IndexStore.load(settings.index_dir, settings)
    import asyncio

    faq = next(c for c in store.chunks if c.chunk_type == "faq")
    vec = asyncio.run(FakeEmbedder().embed([f"{faq.doc_title}: {faq.question}"]))
    hits = store.dense_search(vec, 10)
    assert len({h[0] for h in hits}) == len(hits)
    assert store.chunks[hits[0][0]].chunk_id == faq.chunk_id


def test_bm25_search_finds_foreclosure(built: tuple[Settings, dict[str, object], FakeEmbedder]) -> None:
    settings, _, _ = built
    store = IndexStore.load(settings.index_dir, settings)
    top = [store.chunks[i] for i, _ in store.bm25_search("foreclosure charge after 24 months", 3)]
    assert any(c.section_id == "6.2" and c.doc_id == "personal_loans" for c in top)


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"openai_embed_model": "text-embedding-3-large"}, "model"),
    ],
)
def test_startup_fails_fast_on_embedder_mismatch(
    built: tuple[Settings, dict[str, object], FakeEmbedder], override: dict[str, object], message: str
) -> None:
    settings, _, _ = built
    other = settings.model_copy(update=override)
    with pytest.raises(IndexMismatchError, match=message):
        IndexStore.load(settings.index_dir, other)


def test_manifest_validation_rules(built: tuple[Settings, dict[str, object], FakeEmbedder]) -> None:
    settings, _, _ = built
    manifest = IndexManifest.model_validate_json(
        (settings.index_dir / "manifest.json").read_text(encoding="utf-8")
    )
    validate_manifest(manifest, settings)
    for update, msg in [
        ({"dim": 768}, "dim"),
        ({"normalized": False}, "normalised"),
        ({"embedder_provider": "other"}, "provider"),
    ]:
        bad = manifest.model_copy(update=update)
        with pytest.raises(IndexMismatchError, match=msg):
            validate_manifest(bad, settings)
    tampered = manifest.model_copy(update={"n_chunks": 1})
    with pytest.raises(IndexMismatchError, match="content_hash"):
        validate_manifest(tampered, settings)


def test_query_vector_from_other_embedder_is_rejected(
    built: tuple[Settings, dict[str, object], FakeEmbedder],
) -> None:
    settings, _, _ = built
    store = IndexStore.load(settings.index_dir, settings)
    with pytest.raises(IndexMismatchError, match="dim"):
        store.dense_search(np.ones((1, 768), dtype=np.float32) / np.sqrt(768), 5)
    with pytest.raises(IndexMismatchError, match="normalised"):
        store.dense_search(np.ones((1, 1536), dtype=np.float32), 5)


def test_missing_index_and_partial_copy(
    built: tuple[Settings, dict[str, object], FakeEmbedder], tmp_path: Path
) -> None:
    settings, _, _ = built
    with pytest.raises(IndexNotFoundError, match=r"python -m app.ingest"):
        IndexStore.load(tmp_path / "nope", settings)
    copy = tmp_path / "copy"
    copy.mkdir()
    for f in settings.index_dir.iterdir():
        (copy / f.name).write_bytes(f.read_bytes())
    (copy / "chunks.jsonl").write_text(
        (copy / "chunks.jsonl").read_text(encoding="utf-8").splitlines()[0] + "\n", encoding="utf-8"
    )
    with pytest.raises(IndexMismatchError, match=r"chunks.jsonl"):
        IndexStore.load(copy, settings)


def test_rebuild_is_reproducible_and_uses_cache(
    built: tuple[Settings, dict[str, object], FakeEmbedder], corpus: Corpus
) -> None:
    settings, first, _ = built
    embedder = FakeEmbedder()
    second = build_index(corpus.chunks, settings, provider="openai", embedder=embedder)
    assert second["content_hash"] == first["content_hash"]
    assert embedder.calls == 0 and second["cache_misses"] == 0  # everything served from the on-disk cache


def test_wrong_embedder_flag_rejected(
    built: tuple[Settings, dict[str, object], FakeEmbedder], corpus: Corpus
) -> None:
    settings, _, _ = built
    with pytest.raises(ValueError, match="does not match"):
        build_index(corpus.chunks[:2], settings, provider="other", embedder=FakeEmbedder())


def test_ingest_cli_full_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr("app.ingest.build.get_embedding_provider", lambda s: FakeEmbedder())
    monkeypatch.setattr(ingest_cli, "write_processed", lambda corpus, d: None)  # keep repo files untouched
    assert ingest_cli.main(["--embedder", "openai"], settings=settings) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["chunks"] == 190 and summary["index"]["dim"] == 1536


def test_bm25_roundtrip_and_ranking() -> None:
    bm = BM25.build([["late", "fee", "400"], ["annual", "fee"], ["foreclosure", "charge"]])
    again = BM25.from_dict(json.loads(json.dumps(bm.to_dict())))
    assert again.top_k(["late", "fee"], 2) == bm.top_k(["late", "fee"], 2)
    assert bm.top_k(["late", "fee"], 3)[0][0] == 0
    assert bm.top_k(["missing"], 3) == []


async def test_embedding_cache_hits(tmp_path: Path) -> None:
    cache = EmbeddingCache(tmp_path / "c.sqlite")
    embedder = FakeEmbedder(dim=8)
    first = await cache.embed(embedder, ["a b", "c d"], "document")
    second = await cache.embed(embedder, ["c d", "a b", "e"], "document")
    assert embedder.texts_embedded == 3 and cache.hits == 2 and cache.misses == 3
    np.testing.assert_allclose(second[1], first[0])
    cache.close()
