"""Embed chunks and write the per-embedder index (called by `python -m app.ingest`)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import yaml

from app.ingest.chunker import CHUNKER_VERSION
from app.ingest.embed_cache import EmbeddingCache
from app.ingest.loaders import file_sha256
from app.ingest.models import Chunk
from app.ingest.sources import load_sources
from app.providers.base import EmbeddingProvider
from app.providers.factory import get_embedding_provider
from app.retrieval.store import write_store
from app.settings import Settings
from app.text.tokenize import count_tokens


def vector_inputs(chunks: list[Chunk]) -> tuple[list[str], list[int]]:
    """Texts to embed and their owning chunk index: every chunk (header + text) plus FAQ question-only rows."""
    texts = [c.embed_text for c in chunks]
    owners = list(range(len(chunks)))
    for i, chunk in enumerate(chunks):
        if chunk.chunk_type == "faq" and chunk.question:
            texts.append(f"{chunk.doc_title}: {chunk.question}")
            owners.append(i)
    return texts, owners


def estimate_embedding_cost(tokens: int, model: str, settings: Settings) -> float:
    """USD estimate from config/pricing.yaml."""
    pricing: dict[str, Any] = yaml.safe_load(settings.path(settings.pricing_path).read_text(encoding="utf-8"))
    per_million = float(pricing["embedding"].get(model, pricing["embedding"]["default"]))
    return round(tokens / 1_000_000 * per_million, 6)


async def _embed(provider: EmbeddingProvider, cache: EmbeddingCache, texts: list[str]) -> Any:
    return await cache.embed(provider, texts, "document")


def build_index(
    chunks: list[Chunk],
    settings: Settings,
    *,
    provider: str,
    rebuild: bool = False,
    embedder: EmbeddingProvider | None = None,
    conflicts: list[dict[str, Any]] | None = None,
    missing_sections: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Embed (with cache) and write `indexes/<provider>-<model>/`. Returns a summary."""
    embedder = embedder or get_embedding_provider(settings)
    if embedder.name != provider:
        raise ValueError(f"--embedder {provider} does not match configured EMBED_PROVIDER {embedder.name}")
    cache = EmbeddingCache(settings.path(settings.cache_dir) / "embeddings.sqlite")
    try:
        if rebuild:
            cache.clear()
        texts, owners = vector_inputs(chunks)
        vectors = asyncio.run(_embed(embedder, cache, texts))
        hits, misses = cache.hits, cache.misses
    finally:
        cache.close()
    data_dir = settings.path(settings.data_dir)
    sources = {
        m.doc_id: file_sha256(data_dir / "raw" / m.file) for m in load_sources(data_dir / "sources.yaml")
    }
    directory = settings.index_dir_for(embedder.name, embedder.model)
    manifest = write_store(
        directory,
        chunks,
        vectors,
        owners,
        {
            "embedder_provider": embedder.name,
            "embedder_model": embedder.model,
            "source_pdf_sha256s": sources,
            "chunker_version": CHUNKER_VERSION,
            "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
        },
        conflicts=conflicts,
        missing_sections=missing_sections,
    )
    tokens = sum(count_tokens(t) for t in texts)
    return {
        "dir": str(directory),
        "vectors": int(vectors.shape[0]),
        "dim": manifest.dim,
        "cache_hits": hits,
        "cache_misses": misses,
        "embedded_tokens_est": tokens,
        "est_embedding_cost_usd_full_build": estimate_embedding_cost(tokens, embedder.model, settings),
        "content_hash": manifest.content_hash,
    }
