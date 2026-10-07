"""`python -m app.providers.check`: verify OPENAI_API_KEY and configured models with real (tiny) calls.

Never prints the key. Exit 0 only if the key works and every configured model answers.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

import openai

from app.providers.base import ChatMessage, ProviderError
from app.providers.openai_provider import OpenAIChatProvider, OpenAIEmbeddingProvider, make_client
from app.settings import Settings, get_settings


async def run_checks(settings: Settings) -> dict[str, Any]:
    """Run the checks and return a JSON-serialisable report."""
    report: dict[str, Any] = {"key_present": settings.has_openai_key, "checks": {}}
    if not settings.has_openai_key:
        report["ok"] = False
        report["hint"] = "Copy .env.example to .env and set OPENAI_API_KEY=sk-..."
        return report
    client = make_client(settings, timeout_s=30)
    models = {
        settings.chat_model,
        settings.rewrite_model,
        settings.effective_judge_model,
        settings.embed_model,
    }
    for model in sorted(models):
        try:
            await client.models.retrieve(model)
            report["checks"][f"model:{model}"] = "available"
        except openai.OpenAIError as exc:
            report["checks"][f"model:{model}"] = f"FAILED ({type(exc).__name__})"
    try:
        vectors = await OpenAIEmbeddingProvider(settings, client).embed(["ping"], "query")
        report["checks"]["embedding"] = f"ok (dim={vectors.shape[1]})"
    except ProviderError as exc:
        report["checks"]["embedding"] = f"FAILED ({exc})"
    try:
        result = await OpenAIChatProvider(settings, client).complete(
            [ChatMessage("user", "Reply with OK.")], system="Reply with OK.", max_tokens=16
        )
        report["checks"]["chat"] = (
            f"ok ({result.model}, {result.usage.input_tokens}+{result.usage.output_tokens} tokens)"
        )
    except ProviderError as exc:
        report["checks"]["chat"] = f"FAILED ({exc})"
    report["ok"] = all(not str(v).startswith("FAILED") for v in report["checks"].values())
    return report


def main() -> int:
    """CLI entry point."""
    report = asyncio.run(run_checks(get_settings()))
    sys.stdout.write(json.dumps(report, indent=2) + "\n")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
