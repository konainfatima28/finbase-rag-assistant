"""HTTP routes (PROMPT.md §8), all under `/api`."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from slowapi import Limiter

from app.api.schemas import ChatRequest, FeedbackRequest
from app.api.services import Services
from app.api.sse import SSE_HEADERS, sse_stream
from app.providers.base import ChatMessage
from app.retrieval.context import display_text
from app.safety.pii import redact

VERSION = "1.0.0"


def _services(request: Request) -> Services:
    services: Services = request.app.state.services
    return services


def build_router(limiter: Limiter, rate_limit: str) -> APIRouter:
    """Routes bound to an app-specific limiter."""
    router = APIRouter(prefix="/api")

    @router.post("/chat", summary="Ask the assistant (JSON or SSE when stream=true)")
    @limiter.limit(rate_limit)
    async def chat(request: Request, body: ChatRequest) -> Any:
        services = _services(request)
        history = [ChatMessage(h.role, h.content) for h in body.history]
        if not history and body.session_id:
            history = services.sessions.get(body.session_id) or []
        request_id: str = request.state.request_id

        def remember(result: dict[str, Any]) -> None:
            services.metrics.record(result)
            if body.session_id:
                turns = [
                    *history,
                    ChatMessage("user", redact(body.message).text),
                    ChatMessage("assistant", str(result.get("answer", ""))),
                ]
                services.sessions.set(body.session_id, turns[-services.settings.history_max_messages :])

        if body.stream:
            events = services.answers.stream(body.message, history, request_id)
            stream = sse_stream(events, request.is_disconnected, services.settings.sse_heartbeat_s, remember)
            return StreamingResponse(stream, media_type="text/event-stream", headers=SSE_HEADERS)
        try:
            result = await services.answers.answer(body.message, history, request_id)
        except Exception:
            services.metrics.record_error()
            raise
        remember(result)
        return result

    @router.get("/health", summary="Liveness + configuration (no secrets)")
    async def health(request: Request) -> dict[str, Any]:
        services = _services(request)
        manifest = services.store.manifest
        return {
            "status": "ok",
            "version": VERSION,
            "provider": services.settings.llm_provider,
            "chat_model": services.settings.chat_model,
            "embed_provider": manifest.embedder_provider,
            "embed_model": manifest.embedder_model,
            "reranker": services.reranker.name,
            "reranker_loaded": bool(
                getattr(services.reranker, "available", services.reranker.name == "none")
            ),
            "chunks": len(services.store.chunks),
            "gate_calibrated": services.retriever.gate_config.calibrated,
            "index": manifest.model_dump(exclude={"source_pdf_sha256s"}),
        }

    @router.get("/ready", summary="Readiness: index loaded")
    async def ready(request: Request) -> dict[str, Any]:
        services = _services(request)
        if not services.ready:
            raise HTTPException(status_code=503, detail="not ready")
        return {"ready": True, "index_loaded": True, "chunks": len(services.store.chunks)}

    @router.get("/docs/list", summary="Documents and their sections")
    async def docs_list(request: Request) -> dict[str, Any]:
        services = _services(request)
        docs: dict[str, dict[str, Any]] = {}
        for chunk in services.store.chunks:
            doc = docs.setdefault(
                chunk.doc_id,
                {
                    "doc_id": chunk.doc_id,
                    "title": chunk.doc_title,
                    "code": chunk.doc_code,
                    "effective_date": chunk.effective_date,
                    "sections": {},
                },
            )
            if chunk.chunk_type == "table_row":
                continue
            section = doc["sections"].setdefault(
                chunk.section_id,
                {
                    "section_id": chunk.section_id,
                    "title": chunk.section_title,
                    "page_start": chunk.page_start,
                    "page_end": chunk.page_end,
                    "chunk_types": [],
                },
            )
            section["page_start"] = min(section["page_start"], chunk.page_start)
            section["page_end"] = max(section["page_end"], chunk.page_end)
            if chunk.chunk_type not in section["chunk_types"]:
                section["chunk_types"].append(chunk.chunk_type)
        return {"documents": [{**d, "sections": list(d["sections"].values())} for d in docs.values()]}

    @router.get("/chunks/{chunk_id}", summary="Full source text of a chunk (sources drawer)")
    async def get_chunk(request: Request, chunk_id: str) -> dict[str, Any]:
        chunk = _services(request).store.chunk_by_id(chunk_id)
        if chunk is None:
            raise HTTPException(status_code=404, detail="chunk not found")
        # display text: FAQ entries without internal "(Operational case N)" labels (same text the LLM saw)
        return {**chunk.model_dump(), "text": display_text(chunk), "citation": chunk.citation_label}

    @router.post("/feedback", summary="Thumbs up/down on an answer")
    async def feedback(request: Request, body: FeedbackRequest) -> dict[str, Any]:
        services = _services(request)
        record = {
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "request_id": body.request_id,
            "rating": body.rating,
            "comment": redact(body.comment).text if body.comment else None,
        }
        path = services.settings.path(services.settings.feedback_log_path)
        async with services.feedback_lock:
            await asyncio.to_thread(_append_jsonl, path, record)
        return {"ok": True}

    @router.get("/metrics", summary="Aggregate latency, cache, abstain, cost, error metrics")
    async def metrics(request: Request) -> dict[str, Any]:
        services = _services(request)
        return services.metrics.snapshot(services.caches())

    @router.get("/eval/latest", summary="Latest stored evaluation run")
    async def eval_latest(request: Request) -> dict[str, Any]:
        path = _services(request).settings.path(_services(request).settings.eval_results_dir) / "latest.json"
        if not path.exists():
            raise HTTPException(status_code=404, detail="no evaluation results yet")
        data: dict[str, Any] = json.loads(await asyncio.to_thread(path.read_text, encoding="utf-8"))
        return data

    @router.get("/eval/runs", summary="All stored evaluation runs (summaries)")
    async def eval_runs(request: Request) -> dict[str, Any]:
        directory = _services(request).settings.path(_services(request).settings.eval_results_dir)
        return {"runs": await asyncio.to_thread(_run_summaries, directory)}

    return router


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _run_summaries(directory: Path) -> list[dict[str, Any]]:
    runs = []
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        runs.append(
            {"file": path.name, **{k: data.get(k) for k in ("run_id", "created_at", "config", "summary")}}
        )
    return runs
