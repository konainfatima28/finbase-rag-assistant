"""Request/response models for the HTTP API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.settings import get_settings

_MAX_CHARS = get_settings().max_message_chars


class HistoryItem(BaseModel):
    """One prior conversation turn sent by the client (the API is stateless across restarts)."""

    role: Literal["user", "assistant"]
    content: str = Field(max_length=8000)


class ChatRequest(BaseModel):
    """`POST /api/chat` body."""

    message: str = Field(min_length=1, max_length=_MAX_CHARS)
    history: list[HistoryItem] = Field(default_factory=list, max_length=50)
    session_id: str | None = Field(default=None, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    stream: bool = False

    @field_validator("message")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must not be empty or whitespace")
        return value


class FeedbackRequest(BaseModel):
    """`POST /api/feedback` body."""

    request_id: str = Field(min_length=1, max_length=64)
    rating: Literal["up", "down"]
    comment: str | None = Field(default=None, max_length=1000)
