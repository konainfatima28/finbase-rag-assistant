"""LLM-as-judge (OpenAI, temperature 0, JSON output): correctness 0-2 and claim-level groundedness."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.providers.base import ChatMessage, ChatProvider, ProviderError

PROMPTS = Path(__file__).resolve().parent / "judge_prompts"


class Judge:
    """Thin wrapper around a ChatProvider with the two rubrics."""

    def __init__(self, chat: ChatProvider, model: str) -> None:
        self.chat = chat
        self.model = model
        self.correctness_system = (PROMPTS / "correctness.txt").read_text(encoding="utf-8")
        self.groundedness_system = (PROMPTS / "groundedness.txt").read_text(encoding="utf-8")
        self.input_tokens = 0
        self.output_tokens = 0

    async def _json(self, system: str, user: str) -> dict[str, Any] | None:
        try:
            result = await self.chat.complete(
                [ChatMessage("user", user)],
                system=system,
                max_tokens=700,
                json_mode=True,
                model=self.model,
                timeout_s=60,
            )
        except ProviderError:
            return None
        self.input_tokens += result.usage.input_tokens
        self.output_tokens += result.usage.output_tokens
        try:
            data: dict[str, Any] = json.loads(result.text)
        except json.JSONDecodeError:
            return None
        return data

    async def correctness(self, item: dict[str, Any], answer: str) -> dict[str, Any] | None:
        """{"score": 0..2, "reason": str} or None if the judge failed."""
        reference = "; ".join(item.get("expected_facts", [])) or "(no specific facts)"
        expected_behaviour = (
            "ABSTAIN (say it is not in FinBase's documents)" if not item["answerable"] else "ANSWER"
        )
        user = (
            f"QUESTION: {item['question']}\nEXPECTED BEHAVIOUR: {expected_behaviour}\nREFERENCE FACTS: {reference}\n"
            f"FORBIDDEN CONTENT: {'; '.join(item.get('forbidden_facts', [])) or '(none)'}\nNOTES: {item.get('notes', '')}\n\nANSWER:\n{answer}"
        )
        data = await self._json(self.correctness_system, user)
        if not data or data.get("score") not in (0, 1, 2):
            return None
        return {"score": int(data["score"]), "reason": str(data.get("reason", ""))[:300]}

    async def groundedness(self, answer: str, sources: list[str]) -> dict[str, Any] | None:
        """{"claims": n, "supported": k, "rate": k/n, "unsupported": [...]} or None."""
        if not sources:
            return None
        user = (
            "SOURCES:\n"
            + "\n\n".join(f"[{i}] {s}" for i, s in enumerate(sources, start=1))
            + f"\n\nANSWER:\n{answer}"
        )
        data = await self._json(self.groundedness_system, user)
        if not data or not isinstance(data.get("claims"), list):
            return None
        claims = [c for c in data["claims"] if isinstance(c, dict)]
        if not claims:
            return {"claims": 0, "supported": 0, "rate": 1.0, "unsupported": []}
        supported = [c for c in claims if c.get("supported") is True]
        return {
            "claims": len(claims),
            "supported": len(supported),
            "rate": len(supported) / len(claims),
            "unsupported": [str(c.get("claim", ""))[:200] for c in claims if c.get("supported") is not True],
        }
