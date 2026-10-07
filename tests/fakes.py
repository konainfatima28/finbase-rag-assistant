"""Test doubles. Used ONLY by automated tests — never by the application."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, Sequence

import numpy as np

from app.providers.base import (
    ChatMessage,
    ChatResult,
    EmbedKind,
    ProviderError,
    StreamChunk,
    Usage,
    Vectors,
    l2_normalize,
)
from app.text.tokenize import tokenize


class FakeEmbedder:
    """Deterministic hashing bag-of-words embedder (no network). Mimics OpenAI's identity by default."""

    def __init__(self, name: str = "openai", model: str = "text-embedding-3-small", dim: int = 1536) -> None:
        self.name, self.model, self.dim = name, model, dim
        self.calls = 0
        self.texts_embedded = 0

    def _vector(self, text: str) -> np.ndarray:
        vec = np.zeros(self.dim, dtype=np.float32)
        for token in tokenize(text) or ["<empty>"]:
            h = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16)
            vec[h % self.dim] += 1.0 if (h >> 64) % 2 else -1.0
        return vec

    async def embed(self, texts: Sequence[str], kind: EmbedKind = "document") -> Vectors:
        self.calls += 1
        self.texts_embedded += len(texts)
        return l2_normalize(np.vstack([self._vector(t) for t in texts]))


class FakeLLM:
    """Scripted chat provider. `responder(messages, system)` returns the answer text."""

    name = "openai"
    model = "fake-model"

    def __init__(self, responder=None, fail: bool = False) -> None:  # type: ignore[no-untyped-def]
        self.responder = responder or (lambda messages, system: "NOT_FOUND")
        self.fail = fail
        self.calls: list[dict[str, object]] = []

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
        self.calls.append({"messages": list(messages), "system": system, "json_mode": json_mode})
        if self.fail:
            raise ProviderError("fake provider down")
        return ChatResult(text=self.responder(messages, system), model=self.model, usage=Usage(100, 20))

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        system: str,
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> AsyncIterator[StreamChunk]:
        self.calls.append({"messages": list(messages), "system": system, "stream": True})
        if self.fail:
            raise ProviderError("fake provider down")
        text = self.responder(messages, system)
        for i in range(0, len(text), 7):
            yield StreamChunk(delta=text[i : i + 7])
        yield StreamChunk(done=True, usage=Usage(100, 20))
