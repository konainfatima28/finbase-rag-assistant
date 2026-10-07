"""Contract tests: the real OpenAI provider against mocked HTTP. No network, no key needed.

openai>=3 ships its own `httpx2` transport, which respx cannot patch (verified: requests escaped respx).
We inject `httpx2.MockTransport` through the SDK's supported `http_client=` parameter instead (D-014).
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx2
import numpy as np
import pytest
from openai import AsyncOpenAI

from app.providers.base import (
    ChatMessage,
    ChatProvider,
    EmbeddingProvider,
    ProviderConfigError,
    ProviderError,
)
from app.providers.factory import get_chat_provider, get_embedding_provider
from app.providers.openai_provider import OpenAIChatProvider, OpenAIEmbeddingProvider
from app.settings import Settings

BASE = "https://api.openai.com/v1"
Reply = Callable[[httpx2.Request], httpx2.Response]


class MockOpenAI:
    """Records requests and replays scripted responses (a callable, or a list consumed in order)."""

    def __init__(self, replies: Reply | list[httpx2.Response]) -> None:
        self.replies = replies
        self.requests: list[httpx2.Request] = []

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if isinstance(self.replies, list):
            return self.replies.pop(0)
        return self.replies(request)

    def client(self) -> AsyncOpenAI:
        transport = httpx2.MockTransport(self.handler)
        return AsyncOpenAI(
            api_key="sk-test", max_retries=0, http_client=httpx2.AsyncClient(transport=transport)
        )

    def body(self, i: int = 0) -> dict[str, object]:
        data: dict[str, object] = json.loads(self.requests[i].content)
        return data


def _settings(**kw: object) -> Settings:
    return Settings(openai_api_key="sk-test", llm_max_retries=0, embed_batch_size=2, **kw)  # type: ignore[arg-type]


def _response(text: str, model: str = "gpt-4.1-mini") -> dict[str, object]:
    return {
        "id": "resp_1",
        "object": "response",
        "created_at": 0,
        "status": "completed",
        "model": model,
        "output": [
            {
                "type": "message",
                "id": "msg_1",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }
        ],
        "parallel_tool_calls": False,
        "tool_choice": "auto",
        "tools": [],
        "usage": {
            "input_tokens": 11,
            "output_tokens": 3,
            "total_tokens": 14,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        },
    }


def _sse(events: list[dict[str, object]]) -> bytes:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode("utf-8")


def _delta(seq: int, text: str) -> dict[str, object]:
    return {
        "type": "response.output_text.delta",
        "sequence_number": seq,
        "item_id": "msg_1",
        "output_index": 0,
        "content_index": 0,
        "delta": text,
        "logprobs": [],
    }


def test_factory_returns_openai_implementations() -> None:
    settings = _settings()
    assert isinstance(get_chat_provider(settings), ChatProvider)
    assert isinstance(get_embedding_provider(settings), EmbeddingProvider)
    assert get_chat_provider(settings).model == "gpt-4.1-mini"
    assert get_embedding_provider(settings).model == "text-embedding-3-small"


def test_missing_key_gives_actionable_error() -> None:
    provider = OpenAIChatProvider(Settings(openai_api_key=""))  # type: ignore[arg-type]
    with pytest.raises(ProviderConfigError, match="OPENAI_API_KEY"):
        _ = provider.client


async def test_complete_uses_responses_api_with_temperature_and_no_store() -> None:
    mock = MockOpenAI([httpx2.Response(200, json=_response("Answer: hi [1]"))])
    provider = OpenAIChatProvider(_settings(), mock.client())
    result = await provider.complete([ChatMessage("user", "q")], system="SYS", max_tokens=50, json_mode=True)
    assert result.text == "Answer: hi [1]"
    assert (result.usage.input_tokens, result.usage.output_tokens) == (11, 3)
    assert str(mock.requests[0].url) == f"{BASE}/responses"
    body = mock.body()
    assert body["model"] == "gpt-4.1-mini" and body["instructions"] == "SYS"
    assert body["temperature"] == 0.0 and body["store"] is False and body["max_output_tokens"] == 50
    # live API requires "json" in an input message for json_object mode -> provider appends a line
    assert body["input"] == [
        {"role": "user", "content": "q"},
        {"role": "user", "content": "Respond with a single JSON object."},
    ]
    assert body["text"] == {"format": {"type": "json_object"}}
    assert mock.requests[0].headers["authorization"] == "Bearer sk-test"


async def test_model_rejecting_temperature_is_retried_without_it() -> None:
    error = {"error": {"message": "Unsupported parameter: 'temperature'", "type": "invalid_request_error"}}
    mock = MockOpenAI([httpx2.Response(400, json=error), httpx2.Response(200, json=_response("ok"))])
    provider = OpenAIChatProvider(_settings(), mock.client())
    assert (await provider.complete([ChatMessage("user", "q")], system="s")).text == "ok"
    assert "temperature" in mock.body(0) and "temperature" not in mock.body(1)


async def test_server_error_maps_to_provider_error() -> None:
    mock = MockOpenAI([httpx2.Response(500, json={"error": {"message": "boom"}})])
    with pytest.raises(ProviderError):
        await OpenAIChatProvider(_settings(), mock.client()).complete([ChatMessage("user", "q")], system="s")


async def test_stream_yields_deltas_then_usage() -> None:
    events = [
        {"type": "response.created", "sequence_number": 0, "response": _response("")},
        _delta(1, "Answer: "),
        _delta(2, "3% [1]"),
        {"type": "response.completed", "sequence_number": 3, "response": _response("Answer: 3% [1]")},
    ]
    sse = httpx2.Response(200, content=_sse(events), headers={"content-type": "text/event-stream"})
    mock = MockOpenAI([sse])
    provider = OpenAIChatProvider(_settings(), mock.client())
    chunks = [c async for c in provider.stream([ChatMessage("user", "q")], system="s")]
    assert mock.body()["stream"] is True
    assert "".join(c.delta for c in chunks) == "Answer: 3% [1]"
    assert chunks[-1].done and chunks[-1].usage is not None and chunks[-1].usage.output_tokens == 3


async def test_stream_error_event_raises() -> None:
    events = [
        _delta(1, "par"),
        {"type": "error", "sequence_number": 2, "code": "server_error", "message": "x", "param": None},
    ]
    mock = MockOpenAI(
        [httpx2.Response(200, content=_sse(events), headers={"content-type": "text/event-stream"})]
    )
    with pytest.raises(ProviderError):
        async for _ in OpenAIChatProvider(_settings(), mock.client()).stream(
            [ChatMessage("user", "q")], system="s"
        ):
            pass


async def test_embeddings_are_batched_ordered_and_normalised() -> None:
    def reply(request: httpx2.Request) -> httpx2.Response:
        inputs = json.loads(request.content)["input"]
        data = [
            {"object": "embedding", "index": i, "embedding": [3.0, 4.0] if t == "a" else [1.0, 0.0]}
            for i, t in enumerate(inputs)
        ]
        payload = {
            "object": "list",
            "data": list(reversed(data)),
            "model": "text-embedding-3-small",
            "usage": {"prompt_tokens": 1, "total_tokens": 1},
        }
        return httpx2.Response(200, json=payload)

    mock = MockOpenAI(reply)
    vectors = await OpenAIEmbeddingProvider(_settings(), mock.client()).embed(["a", "b", "a"], "document")
    assert len(mock.requests) == 2  # batch size 2
    assert str(mock.requests[0].url) == f"{BASE}/embeddings"
    assert mock.body()["model"] == "text-embedding-3-small"
    assert vectors.dtype == np.float32 and vectors.shape == (3, 2)
    np.testing.assert_allclose(vectors[0], [0.6, 0.8], rtol=1e-6)
    np.testing.assert_allclose(np.linalg.norm(vectors, axis=1), 1.0, rtol=1e-6)


async def test_embedding_failure_maps_to_provider_error() -> None:
    mock = MockOpenAI([httpx2.Response(401, json={"error": {"message": "bad key"}})])
    with pytest.raises(ProviderError):
        await OpenAIEmbeddingProvider(_settings(), mock.client()).embed(["x"], "query")


async def test_no_real_network_in_tests() -> None:
    provider = OpenAIEmbeddingProvider(_settings())  # real transport, no mock
    with pytest.raises(RuntimeError, match="network access blocked in tests"):
        await provider.embed(["x"], "query")
