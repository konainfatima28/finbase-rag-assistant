"""Pre-RAG intent routing: a bare greeting/closing/thanks message never needs the rewrite, retrieval
(FAISS/BM25/RRF/FlashRank) or LLM-generation steps, so `AnswerService` answers it directly (PROMPT.md's
NOT_FOUND, grounding, citation and product-scope behavior is untouched for every other message).

Matching is on the WHOLE normalized message, never a prefix: "Hi, what documents do I need to open a
savings account?" normalizes to extra words outside `_GREETING_PHRASES` and is correctly left to the
normal pipeline, while "Hi" / "Hi!" / "hi there" normalize to a listed phrase.
"""

from __future__ import annotations

import re

GREETING_REPLY = (
    "Hi! I'm FinBase Assistant. How can I help you with your financial services or account-related questions?"
)

_GREETING_PHRASES = frozenset(
    {
        "hi",
        "hello",
        "hey",
        "hiya",
        "yo",
        "hi there",
        "hello there",
        "hey there",
        "good morning",
        "good afternoon",
        "good evening",
        "good day",
        "thanks",
        "thank you",
        "thanks a lot",
        "thank you so much",
        "thank you very much",
        "many thanks",
        "much appreciated",
        "ty",
        "cheers",
        "ok thanks",
        "okay thanks",
        "ok thank you",
        "okay thank you",
        "great thanks",
        "great thank you",
    }
)
_STRIP_RE = re.compile(r"[^a-z\s]")


def normalize(text: str) -> str:
    """Lowercase; punctuation/emoji removed; whitespace collapsed."""
    return re.sub(r"\s+", " ", _STRIP_RE.sub(" ", text.lower())).strip()


def is_greeting(text: str) -> bool:
    """True only when the entire message is a greeting/closing/thanks phrase."""
    return normalize(text) in _GREETING_PHRASES
