"""Pre-RAG greeting/casual-message router (app.safety.intent)."""

from __future__ import annotations

import pytest

from app.safety.intent import is_greeting


@pytest.mark.parametrize(
    "text",
    [
        "Hi",
        "hi",
        "Hi!",
        "Hi there",
        "Hello",
        "hello!!",
        "Hey",
        "Good morning",
        "Good evening!",
        "Thanks",
        "thank you",
        "Thank you so much",
        "many thanks",
        "ok thanks",
        "  hi  ",
    ],
)
def test_greetings_detected(text: str) -> None:
    assert is_greeting(text)


@pytest.mark.parametrize(
    "text",
    [
        "Hi, what documents do I need to open a savings account?",
        "Hello, what is the foreclosure charge?",
        "What is the home loan interest rate?",
        "Hi there, I have a question about my loan",
        "Thanks, but what about the processing fee?",
        "good morning team, is the branch open on Saturday?",
    ],
)
def test_genuine_questions_not_treated_as_greetings(text: str) -> None:
    assert not is_greeting(text)
