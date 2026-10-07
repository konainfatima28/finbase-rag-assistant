"""Process-wide service container, built once at startup (fail fast on index/embedder mismatch)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import structlog

from app.cache.ttl_lru import TTLLRUCache
from app.generation.answer import AnswerService
from app.generation.prompts import load_prompts
from app.observability.costs import load_pricing
from app.observability.metrics import Metrics
from app.providers.base import ChatMessage, ChatProvider, EmbeddingProvider
from app.providers.factory import get_chat_provider, get_embedding_provider
from app.retrieval.context import ConflictGroup
from app.retrieval.gate import GateConfig
from app.retrieval.pipeline import Retriever
from app.retrieval.rerank import FlashRankReranker, Reranker, make_reranker
from app.retrieval.store import IndexStore
from app.settings import Settings

log = structlog.get_logger(__name__)


@dataclass
class Services:
    """Everything the routes need."""

    settings: Settings
    store: IndexStore
    retriever: Retriever
    answers: AnswerService
    reranker: Reranker
    metrics: Metrics = field(default_factory=Metrics)
    sessions: TTLLRUCache[list[ChatMessage]] = field(default_factory=lambda: TTLLRUCache(1000, 3600))
    feedback_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    ready: bool = True

    def caches(self) -> dict[str, Any]:
        """Named caches for metrics."""
        return {
            "retrieval": self.retriever.results,
            "query_embeddings": self.retriever.query_vectors,
            "answers": self.answers.answers,
            "sessions": self.sessions,
        }


def build_services(
    settings: Settings,
    *,
    chat: ChatProvider | None = None,
    embedder: EmbeddingProvider | None = None,
    reranker: Reranker | None = None,
) -> Services:
    """Load + validate the index (raises IndexNotFoundError / IndexMismatchError) and wire the pipeline."""
    store = IndexStore.load(settings.index_dir, settings)
    gate = GateConfig.from_dict(settings.load_thresholds())
    reranker = reranker or make_reranker(
        settings.reranker,
        settings.reranker_model,
        settings.path(settings.cache_dir) / "flashrank",
        settings.rerank_batch_size,
    )
    if isinstance(reranker, FlashRankReranker):
        reranker.load()  # warm at startup; failure degrades to fused order (logged)
    conflicts = [
        ConflictGroup(g["doc_id"], g["category"], list(g["members"]), g.get("detail", ""))
        for g in store.conflicts
    ]
    retriever = Retriever(
        store, embedder or get_embedding_provider(settings), reranker, settings, gate, conflicts
    )
    prompts = load_prompts(settings.path(settings.prompts_dir), settings.prompt_version)
    answers = AnswerService(
        settings,
        retriever,
        chat or get_chat_provider(settings),
        prompts,
        gate,
        load_pricing(settings.path(settings.pricing_path)),
    )
    log.info(
        "services_ready",
        index=str(settings.index_dir.name),
        chunks=len(store.chunks),
        embedder=f"{store.manifest.embedder_provider}/{store.manifest.embedder_model}",
        chat_model=settings.chat_model,
        reranker=reranker.name,
        gate_calibrated=gate.calibrated,
    )
    return Services(
        settings, store, retriever, answers, reranker, sessions=TTLLRUCache(1000, settings.session_ttl_s)
    )
