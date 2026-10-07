"""`python -m eval.perf`: measured latency/cost of the optimisation levers (real OpenAI calls).

Paths measured end-to-end through AnswerService (5 runs each, p50):
  answer (uncached) -> answer (answer-cache hit) -> gate abstain (no LLM call) -> follow-up (rewrite call)
Writes eval/results/perf.json.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import sys
import time
from typing import Any

from app.api.services import build_services
from app.providers.base import ChatMessage
from app.settings import ROOT_DIR, get_settings

RUNS = 5


async def measure() -> dict[str, Any]:
    services = build_services(get_settings())
    answers = services.answers
    out: dict[str, Any] = {}

    async def timed(
        label: str, message: str, history: list[ChatMessage] | None = None, clear: bool = True
    ) -> None:
        lat, cost, cached, answerable = [], [], [], []
        for _ in range(RUNS):
            if clear:
                answers.answers.clear()
                services.retriever.results.clear()
                services.retriever.query_vectors.clear()
            started = time.perf_counter()
            result = await answers.answer(message, history or [])
            lat.append((time.perf_counter() - started) * 1000)
            cost.append(result["usage"]["cost_usd"])
            cached.append(result["cached"])
            answerable.append(result["answerable"])
        out[label] = {
            "p50_ms": round(statistics.median(lat), 1),
            "min_ms": round(min(lat), 1),
            "max_ms": round(max(lat), 1),
            "cost_usd_mean": round(statistics.mean(cost), 6),
            "cached_runs": sum(cached),
            "answerable": answerable[0],
        }

    question = "What is the foreclosure charge if I close my personal loan after 18 months?"
    await timed("answer_uncached", question)
    await answers.answer(question)  # warm the caches
    await timed("answer_cache_hit", question, clear=False)
    await timed("gate_abstain_no_llm", "What is today's RBI repo rate?")
    history = [
        ChatMessage("user", question),
        ChatMessage("assistant", "3% of the outstanding principal [1]."),
    ]
    await timed("follow_up_with_rewrite", "What if I close it after 2 years instead?", history)
    return out


def main() -> int:
    report = asyncio.run(measure())
    path = ROOT_DIR / "eval" / "results" / "perf.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    sys.stdout.write(json.dumps(report, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
