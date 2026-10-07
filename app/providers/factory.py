"""Provider factory: the single place that maps settings to implementations."""

from __future__ import annotations

from app.providers.base import ChatProvider, EmbeddingProvider, ProviderConfigError
from app.providers.openai_provider import OpenAIChatProvider, OpenAIEmbeddingProvider
from app.settings import Settings


def get_chat_provider(settings: Settings) -> ChatProvider:
    """Chat provider for `LLM_PROVIDER` (only `openai` is implemented — DECISIONS D-011)."""
    if settings.llm_provider == "openai":
        return OpenAIChatProvider(settings)
    raise ProviderConfigError(f"unsupported LLM_PROVIDER: {settings.llm_provider}")


def get_embedding_provider(settings: Settings) -> EmbeddingProvider:
    """Embedding provider for `EMBED_PROVIDER` (defaults to `LLM_PROVIDER`)."""
    if settings.effective_embed_provider == "openai":
        return OpenAIEmbeddingProvider(settings)
    raise ProviderConfigError(f"unsupported EMBED_PROVIDER: {settings.effective_embed_provider}")
