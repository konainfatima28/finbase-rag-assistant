"""Server-Sent Events (PROMPT.md §8): heartbeat comments, client-disconnect cancellation, error events."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import structlog

from app.generation.answer import Event

log = structlog.get_logger(__name__)

SSE_HEADERS = {"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"}
_DONE = object()


def format_event(event: str, data: Any) -> str:
    """One SSE frame. JSON payload on a single `data:` line (newlines are JSON-escaped)."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def sse_stream(
    events: AsyncIterator[Event],
    is_disconnected: Callable[[], Awaitable[bool]],
    heartbeat_s: float,
    on_done: Callable[[dict[str, Any]], None] | None = None,
) -> AsyncIterator[str]:
    """Pump `events` into SSE frames; heartbeat while idle; cancel upstream if the client goes away."""
    queue: asyncio.Queue[object] = asyncio.Queue()

    async def produce() -> None:
        try:
            async for event in events:
                await queue.put(event)
        except Exception as exc:  # never crash the stream: report as an `error` event
            log.error("stream_failed", error=type(exc).__name__)
            await queue.put(
                Event(
                    "error",
                    {
                        "code": "internal_error",
                        "message": "Something went wrong while generating the answer.",
                    },
                )
            )
        finally:
            await queue.put(_DONE)

    task = asyncio.create_task(produce())
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=heartbeat_s)
            except TimeoutError:
                if await is_disconnected():
                    log.info("client_disconnected")
                    break
                yield ": heartbeat\n\n"
                continue
            if item is _DONE:
                break
            assert isinstance(item, Event)
            if item.type == "done" and on_done is not None:
                on_done(item.data)
            yield format_event(item.type, item.data)
    finally:
        if not task.done():
            task.cancel()  # propagates into the provider stream, which closes the upstream HTTP stream
            with contextlib.suppress(asyncio.CancelledError):
                await task
