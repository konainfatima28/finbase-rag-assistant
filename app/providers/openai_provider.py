"""OpenAI provider: Responses API for generation/streaming, Embeddings API for vectors.

Async client, explicit timeouts, SDK exponential-backoff retries (`max_retries`), `store=False` so
customer text is not retained for later retrieval. If a configured model rejects `temperature`
(some reasoning models do), the call is retried once without it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

import numpy as np
import openai
import structlog
from openai import AsyncOpenAI

from app.providers.base import (
    ChatMessage,
    ChatResult,
    EmbedKind,
    ProviderConfigError,
    ProviderError,
    StreamChunk,
    Usage,
    Vectors,
    l2_normalize,
)
from app.settings import Settings

log = structlog.get_logger(__name__)


def make_client(settings: Settings, timeout_s: float | None = None) -> AsyncOpenAI:
    """Build an AsyncOpenAI client from settings (raises if the key is missing)."""
    if not settings.has_openai_key or settings.openai_api_key is None:
        raise ProviderConfigError(
            "OPENAI_API_KEY is not set. Add it to .env (local) or the host environment (Render)."
        )
    return AsyncOpenAI(
        api_key=settings.openai_api_key.get_secret_value(),
        base_url=settings.openai_base_url or None,
        timeout=timeout_s or settings.llm_timeout_s,
        max_retries=settings.llm_max_retries,
    )


def _input(messages: Sequence[ChatMessage]) -> list[dict[str, str]]:
    return [{"role": m.role, "content": m.content} for m in messages]


def _usage(raw: Any) -> Usage:
    if raw is None:
        return Usage()
    return Usage(
        input_tokens=int(getattr(raw, "input_tokens", 0) or 0),
        output_tokens=int(getattr(raw, "output_tokens", 0) or 0),
    )


def _rejects_temperature(exc: openai.BadRequestError) -> bool:
    return "temperature" in str(exc).lower()


class OpenAIChatProvider:
    """`ChatProvider` backed by the OpenAI Responses API."""

    name = "openai"

    def __init__(self, settings: Settings, client: AsyncOpenAI | None = None) -> None:
        self.settings = settings
        self.model = settings.chat_model
        self._client = client
        self._send_temperature = True

    @property
    def client(self) -> AsyncOpenAI:
        """Lazily created client (lets the app start and report a clear error without a key)."""
        if self._client is None:
            self._client = make_client(self.settings)
        return self._client

    def _params(
        self,
        messages: Sequence[ChatMessage],
        system: str,
        max_tokens: int | None,
        model: str | None,
        json_mode: bool,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": model or self.model,
            "instructions": system,
            "input": _input(messages),
            "max_output_tokens": max_tokens or self.settings.max_tokens,
            "store": False,
        }
        if self._send_temperature:
            params["temperature"] = self.settings.temperature
        if json_mode:
            params["text"] = {"format": {"type": "json_object"}}
            # The Responses API rejects json_object mode unless an INPUT message mentions "json"
            # (instructions do not count) -> 400 observed against the live API.
            if not any("json" in m.content.lower() for m in messages):
                params["input"] = [
                    *params["input"],
                    {"role": "user", "content": "Respond with a single JSON object."},
                ]
        return params

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        system: str,
        max_tokens: int | None = None,
        json_mode: bool = False,
        model: str | None = None,
        timeout_s: float | None = None,
    ) -> ChatResult:
        """Non-streamed completion."""
        params = self._params(messages, system, max_tokens, model, json_mode)
        client = self.client.with_options(timeout=timeout_s) if timeout_s else self.client
        try:
            try:
                response = await client.responses.create(**params)
            except openai.BadRequestError as exc:
                if not (self._send_temperature and _rejects_temperature(exc)):
                    raise
                log.warning("model_rejects_temperature", model=params["model"])
                self._send_temperature = False
                params.pop("temperature", None)
                response = await client.responses.create(**params)
        except openai.OpenAIError as exc:
            raise ProviderError(f"OpenAI completion failed: {type(exc).__name__}") from exc
        return ChatResult(
            text=response.output_text or "", model=str(response.model), usage=_usage(response.usage)
        )

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        system: str,
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Stream text deltas; closing the generator (client disconnect) closes the upstream stream."""
        params = self._params(messages, system, max_tokens, model, json_mode=False)
        try:
            try:
                stream = await self.client.responses.create(stream=True, **params)
            except openai.BadRequestError as exc:
                if not (self._send_temperature and _rejects_temperature(exc)):
                    raise
                self._send_temperature = False
                params.pop("temperature", None)
                stream = await self.client.responses.create(stream=True, **params)
            usage = Usage()
            try:
                async for event in stream:
                    etype = getattr(event, "type", "")
                    if etype == "response.output_text.delta":
                        yield StreamChunk(delta=str(event.delta))
                    elif etype == "response.completed":
                        usage = _usage(getattr(event.response, "usage", None))
                    elif etype in ("response.failed", "error"):
                        raise ProviderError("OpenAI stream reported an error event")
            finally:
                await stream.close()
            yield StreamChunk(done=True, usage=usage)
        except openai.OpenAIError as exc:
            raise ProviderError(f"OpenAI stream failed: {type(exc).__name__}") from exc


class OpenAIEmbeddingProvider:
    """`EmbeddingProvider` backed by the OpenAI Embeddings API (batched, normalised)."""

    name = "openai"

    def __init__(self, settings: Settings, client: AsyncOpenAI | None = None) -> None:
        self.settings = settings
        self.model = settings.embed_model
        self._client = client

    @property
    def client(self) -> AsyncOpenAI:
        """Lazily created client."""
        if self._client is None:
            self._client = make_client(self.settings)
        return self._client

    async def embed(self, texts: Sequence[str], kind: EmbedKind = "document") -> Vectors:
        """Embed in batches of `embed_batch_size`; OpenAI models need no task prefix."""
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        rows: list[list[float]] = []
        batch = max(1, self.settings.embed_batch_size)
        try:
            for start in range(0, len(texts), batch):
                part = [t.replace("\n", " ") or " " for t in texts[start : start + batch]]
                response = await self.client.embeddings.create(model=self.model, input=part)
                rows.extend(item.embedding for item in sorted(response.data, key=lambda d: d.index))
        except openai.OpenAIError as exc:
            raise ProviderError(f"OpenAI embedding failed: {type(exc).__name__}") from exc
        return l2_normalize(np.asarray(rows, dtype=np.float32))
