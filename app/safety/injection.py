"""Prompt-injection defences (deterministic layers around system-prompt rule 10).

1. `detect` flags instruction-like text in the user message (logged, and figures quoted from such a
   message are not accepted as "from the user's question" by the figure verifier).
2. `neutralize_context` removes instruction-like lines from retrieved chunks before they reach the LLM
   (the real corpus contains none; this protects against a poisoned document).
3. `leaks_system_prompt` blocks an answer that reproduces the system prompt.
"""

from __future__ import annotations

import re

_PATTERNS = re.compile(
    r"ignore (?:all |any |the )?(?:previous|prior|above|earlier|your)\s+(?:instructions|rules|prompts?)"
    r"|disregard (?:all |the |your )?(?:previous |prior |above )?(?:instructions|rules)"
    r"|forget (?:all |your |the )?(?:previous )?(?:instructions|rules)"
    r"|(?:reveal|print|show|repeat|output|tell me)\s+(?:me\s+)?(?:your|the)\s+(?:system\s+)?(?:prompt|instructions|rules)"
    r"|system prompt|developer mode|jailbreak|\bDAN\b"
    r"|you are now\b|from now on you\b|act as (?:an? )?(?!customer)|role-?play"
    r"|pretend (?:that |the |you )|new instructions?:"
    r"|override (?:the |your )?(?:rules|policy|instructions)",
    re.I,
)


def detect(text: str) -> list[str]:
    """Instruction-like phrases found in `text` (empty = none)."""
    return [m.group(0) for m in _PATTERNS.finditer(text)]


def neutralize_context(text: str) -> str:
    """Drop lines of a retrieved chunk that look like instructions to the model."""
    lines = text.split("\n")
    kept = [
        line if not _PATTERNS.search(line) else "[instruction-like text removed from source]"
        for line in lines
    ]
    return "\n".join(kept)


def leaks_system_prompt(output: str, system_prompt: str, window: int = 8) -> bool:
    """True if the output contains any `window`-word run of the system prompt (verbatim leak)."""
    words = re.findall(r"\w+", system_prompt.lower())
    out = " ".join(re.findall(r"\w+", output.lower()))
    if len(words) < window:
        return False
    return any(" ".join(words[i : i + window]) in out for i in range(len(words) - window + 1))
