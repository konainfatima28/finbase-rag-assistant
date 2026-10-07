"""Provider interfaces. The rest of the app depends only on these protocols (OpenAI is the one implementation)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

import numpy as np
import numpy.typing as npt

Role = Literal["user", "assistant"]
EmbedKind = Literal["document", "query"]
Vectors = npt.NDArray[np.float32]


@dataclass(frozen=True)
class ChatMessage:
    """One conversation turn (system instructions are passed separately)."""

    role: Role
    content: str


@dataclass
class Usage:
    """Token usage of one call."""

    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class ChatResult:
    """Non-streamed completion."""

    text: str
    model: str
    usage: Usage = field(default_factory=Usage)


@dataclass
class StreamChunk:
    """A streamed text delta; the final chunk carries `usage` and `done=True`."""

    delta: str = ""
    done: bool = False
    usage: Usage | None = None


class ProviderError(RuntimeError):
    """Provider call failed after retries (network, auth, rate-limit, server)."""


class ProviderConfigError(ProviderError):
    """Provider is not configured (e.g. missing API key)."""


@runtime_checkable
class ChatProvider(Protocol):
    """Chat/generation provider."""

    name: str
    model: str

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
        """Return the full completion."""
        ...

    def stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        system: str,
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Yield text deltas, then one final chunk with usage."""
        ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Embedding provider. Returned vectors are float32 and L2-normalised."""

    name: str
    model: str

    async def embed(self, texts: Sequence[str], kind: EmbedKind) -> Vectors:
        """Embed texts (`kind` lets providers apply task prefixes; OpenAI needs none)."""
        ...


def l2_normalize(vectors: Vectors) -> Vectors:
    """Row-wise L2 normalisation (zero rows left as zeros)."""
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    normalized: Vectors = (vectors / norms).astype(np.float32)
    return normalized
