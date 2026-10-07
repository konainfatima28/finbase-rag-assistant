"""Query rewriting / translation (PROMPT.md §6.2): one short JSON call, only when needed, raw-query fallback.

When is the (cheap) rewrite call made?
  * follow-ups (history present) — to resolve references;
  * any message that is not confidently plain English. "Plain English" is decided by vocabulary, not by
    script: every word must be a common English word or a word of the FinBase corpus. Romanised Hinglish
    ("Savings account kholne ke liye kya documents chahiye?") therefore goes through translation even
    though it has no Devanagari;
  * broad questions (requirements / documents / eligibility / process ...) — the same call returns up to
    3 sub-queries for multi-query retrieval. Sub-queries are accepted only when the question is broad.

A clear English first-turn question is never rewritten: retrieval uses it verbatim.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

import structlog

from app.providers.base import ChatMessage, ChatProvider, ProviderError, Usage

log = structlog.get_logger(__name__)

DEVANAGARI = re.compile(r"[ऀ-ॿ]")
#: frequent Hinglish function words; >= 2 distinct hits => labelled Hinglish (DECISIONS D-016)
HINGLISH = re.compile(
    r"\b(kya|kitna|kitni|kitne|hai|hain|mera|meri|mere|mujhe|kaise|kab|kyun|kyon|nahi|nahin|karna|karein|chahiye|"
    r"batao|bataiye|aur|wala|wali|liye|agar|toh|lagega|milega|sakta|sakti|kar|ke|ka|ki|ko|se|mein|kholne|kholna)\b",
    re.I,
)
#: broad questions that usually need evidence from several sections/documents
BROAD = re.compile(
    r"\b(requirements?|required|documents?|documentation|eligib\w*|criteria|prerequisites?|procedure|process|"
    r"steps|what (?:do|should) i need|need to (?:open|apply|provide|submit)|how (?:do|can|to|should) (?:i |we )?"
    r"(?:open|apply|get|start|register)|open(?:ing)? (?:a |an |my )?(?:\w+ )?account|overview|compare|comparison|"
    r"difference between)\b",
    re.I,
)
#: common English words (function words, question words, everyday verbs) — the corpus adds domain words
COMMON_ENGLISH = frozenset(
    [
        "a",
        "about",
        "above",
        "after",
        "again",
        "against",
        "all",
        "am",
        "an",
        "and",
        "any",
        "are",
        "as",
        "at",
        "be",
        "because",
        "been",
        "before",
        "being",
        "below",
        "between",
        "both",
        "but",
        "by",
        "can",
        "cannot",
        "could",
        "did",
        "do",
        "does",
        "doing",
        "done",
        "down",
        "during",
        "each",
        "either",
        "else",
        "enough",
        "even",
        "ever",
        "every",
        "few",
        "for",
        "from",
        "further",
        "get",
        "gets",
        "getting",
        "give",
        "given",
        "go",
        "going",
        "got",
        "had",
        "has",
        "have",
        "having",
        "he",
        "her",
        "here",
        "hers",
        "him",
        "his",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "itself",
        "just",
        "know",
        "last",
        "least",
        "less",
        "let",
        "like",
        "made",
        "make",
        "many",
        "may",
        "me",
        "might",
        "mine",
        "more",
        "most",
        "much",
        "must",
        "my",
        "myself",
        "need",
        "needed",
        "needs",
        "never",
        "new",
        "next",
        "no",
        "nor",
        "not",
        "now",
        "of",
        "off",
        "often",
        "on",
        "once",
        "one",
        "only",
        "or",
        "other",
        "our",
        "ours",
        "out",
        "over",
        "own",
        "please",
        "put",
        "rather",
        "really",
        "said",
        "same",
        "say",
        "see",
        "shall",
        "she",
        "should",
        "show",
        "since",
        "so",
        "some",
        "still",
        "such",
        "sure",
        "take",
        "tell",
        "than",
        "thank",
        "thanks",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "thing",
        "things",
        "this",
        "those",
        "though",
        "through",
        "till",
        "to",
        "too",
        "under",
        "until",
        "up",
        "upon",
        "us",
        "use",
        "used",
        "using",
        "very",
        "want",
        "wanted",
        "wants",
        "was",
        "we",
        "well",
        "were",
        "what",
        "whatever",
        "when",
        "where",
        "whether",
        "which",
        "while",
        "who",
        "whom",
        "whose",
        "why",
        "will",
        "with",
        "within",
        "without",
        "would",
        "yes",
        "yet",
        "you",
        "your",
        "yours",
        "yourself",
        "hi",
        "hello",
        "hey",
        "okay",
        "ok",
        "also",
        "get",
        "am",
        "pm",
        "per",
        "vs",
        "etc",
        "i'm",
        "it's",
        "don't",
        "can't",
        "won't",
        "what's",
        "i'd",
        "happen",
        "happens",
        "happened",
        "possible",
        "able",
        "allowed",
        "long",
        "often",
        "time",
        "times",
        "way",
        "ways",
        "help",
        "explain",
        "mean",
        "means",
    ]
)


@dataclass
class Rewrite:
    """Standalone English query used for retrieval (+ sub-queries for broad questions)."""

    query: str
    language: str
    used_llm: bool
    usage: Usage
    error: str | None = None
    subqueries: list[str] = field(default_factory=list)


def detect_language(message: str) -> str:
    """Cheap label: 'hi' (Devanagari), 'hinglish', or 'en' (used for the response metadata only)."""
    if DEVANAGARI.search(message):
        return "hi"
    if len({m.group(0).lower() for m in HINGLISH.finditer(message)}) >= 2:
        return "hinglish"
    return "en"


def corpus_vocabulary(texts: Iterable[str]) -> frozenset[str]:
    """Lower-case words of the knowledge base (used to recognise plain-English questions)."""
    vocab: set[str] = set()
    for text in texts:
        vocab.update(w.lower() for w in re.findall(r"[A-Za-z]+", text))
    return frozenset(vocab)


def _stem(word: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def is_plain_english(message: str, vocabulary: frozenset[str] = frozenset()) -> bool:
    """True only when every word is a common English word or a corpus word (no other script)."""
    if re.search(r"[^\x00-\x7F₹’‘“”–—…]", message):
        return False  # Devanagari or any other non-Latin script
    for raw in re.findall(r"[A-Za-z]+(?:'[a-z]+)?", message):
        word = re.sub(r"'s$", "", raw.lower())
        if word in COMMON_ENGLISH or word in vocabulary:
            continue
        stem = _stem(word)
        if stem in vocabulary or any(stem + e in vocabulary for e in ("", "e", "s", "ing", "ed")):
            continue
        return False
    return True


def is_broad(text: str) -> bool:
    """Requirement / document / eligibility / process questions (candidates for sub-queries)."""
    return bool(BROAD.search(text))


def needs_rewrite(
    message: str, history: Sequence[ChatMessage], vocabulary: frozenset[str] = frozenset()
) -> bool:
    """Follow-ups, anything not confidently plain English, and broad questions."""
    return bool(history) or not is_plain_english(message, vocabulary) or is_broad(message)


def _history_text(history: Sequence[ChatMessage], turns: int = 4) -> str:
    recent = list(history)[-turns:]
    return "\n".join(f"{m.role.upper()}: {m.content[:600]}" for m in recent)


def _clean_subqueries(raw: object, limit: int) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        text = " ".join(str(item).split())[:200]
        if text and text.lower() not in {o.lower() for o in out}:
            out.append(text)
    return out[:limit]


async def rewrite_query(
    chat: ChatProvider,
    system: str,
    message: str,
    history: Sequence[ChatMessage],
    *,
    model: str | None,
    timeout_s: float,
    vocabulary: frozenset[str] = frozenset(),
    max_subqueries: int = 3,
) -> Rewrite:
    """Standalone English query (+ sub-queries), or the raw message on any failure (never blocks answering)."""
    language = detect_language(message)
    plain_english = is_plain_english(message, vocabulary)
    if not needs_rewrite(message, history, vocabulary):
        return Rewrite(message, language, used_llm=False, usage=Usage())
    prompt = f"HISTORY:\n{_history_text(history) or '(none)'}\n\nLATEST MESSAGE: {message}"
    try:
        result = await chat.complete(
            [ChatMessage("user", prompt)],
            system=system,
            max_tokens=250,
            json_mode=True,
            model=model,
            timeout_s=timeout_s,
        )
        data = json.loads(result.text)
        query = str(data.get("standalone_query_en", "")).strip()
        lang = str(data.get("language", language)).strip() or language
        if not query:
            raise ValueError("empty standalone_query_en")
        if plain_english and not history:
            query, lang = message, "en"  # clear English is used verbatim, never paraphrased
        broad = is_broad(message) or is_broad(query)
        subqueries = _clean_subqueries(data.get("subqueries"), max_subqueries) if broad else []
        return Rewrite(query, lang, used_llm=True, usage=result.usage, subqueries=subqueries)
    except (ProviderError, ValueError, json.JSONDecodeError, AttributeError) as exc:
        log.warning("rewrite_failed_fallback_raw", error=type(exc).__name__)
        return Rewrite(message, language, used_llm=False, usage=Usage(), error=type(exc).__name__)
