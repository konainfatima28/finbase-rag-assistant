"""FastAPI application: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`."""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.routes import VERSION, build_router
from app.api.services import Services, build_services
from app.observability.logging import configure_logging
from app.settings import Settings, get_settings

log = structlog.get_logger(__name__)

MAX_BODY_BYTES = 64 * 1024


class RequestContextMiddleware:
    """Pure-ASGI middleware (safe for streaming): request id, body-size cap, access log with request_id."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        incoming = headers.get(b"x-request-id", b"").decode("latin-1")
        request_id = (
            incoming if incoming.replace("-", "").isalnum() and len(incoming) <= 64 else uuid.uuid4().hex
        )
        scope.setdefault("state", {})["request_id"] = request_id
        structlog.contextvars.bind_contextvars(request_id=request_id)
        started = time.perf_counter()
        status = {"code": 500}
        length = headers.get(b"content-length")
        if length is not None and length.isdigit() and int(length) > MAX_BODY_BYTES:
            response = JSONResponse({"error": "payload_too_large", "request_id": request_id}, status_code=413)
            await response(scope, receive, send)
            structlog.contextvars.clear_contextvars()
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
                message.setdefault("headers", []).append((b"x-request-id", request_id.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            log.info(
                "http_request",
                method=scope.get("method"),
                path=scope.get("path"),
                status=status["code"],
                duration_ms=round((time.perf_counter() - started) * 1000, 1),
            )
            structlog.contextvars.clear_contextvars()


def create_app(settings: Settings | None = None, services: Services | None = None) -> FastAPI:
    """App factory. `services` is injected by tests; production builds them at startup (fail fast)."""
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if services is None:
            app.state.services = build_services(settings)  # raises with an actionable message on any mismatch
        yield

    app = FastAPI(
        title="FinBase Support Assistant API",
        version=VERSION,
        description="Grounded RAG answers from FinBase policy documents, with structural citations.",
        lifespan=lifespan,
    )
    if services is not None:
        app.state.services = services
    limiter = Limiter(key_func=get_remote_address, headers_enabled=False)
    app.state.limiter = limiter

    @app.exception_handler(RateLimitExceeded)
    async def rate_limited(request: Request, exc: RateLimitExceeded) -> JSONResponse:
        return JSONResponse(
            {
                "error": "rate_limited",
                "detail": f"Too many requests ({exc.detail}). Please wait a moment.",
                "request_id": getattr(request.state, "request_id", None),
            },
            status_code=429,
        )

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [{"loc": list(e.get("loc", [])), "msg": e.get("msg", "")} for e in exc.errors()]
        return JSONResponse(
            {
                "error": "invalid_request",
                "detail": errors,
                "request_id": getattr(request.state, "request_id", None),
            },
            status_code=422,
        )

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.error("unhandled_error", error_type=type(exc).__name__, path=request.url.path)
        return JSONResponse(
            {
                "error": "internal_error",
                "detail": "Something went wrong. Please try again.",
                "request_id": getattr(request.state, "request_id", None),
            },
            status_code=500,
        )

    app.include_router(build_router(limiter, settings.rate_limit))

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, Any]:
        return {"service": "finbase-assistant", "docs": "/docs", "health": "/api/health"}

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
        max_age=600,
    )
    app.add_middleware(RequestContextMiddleware)
    return app


def _lazy_app() -> FastAPI:
    return create_app()


app = _lazy_app()
