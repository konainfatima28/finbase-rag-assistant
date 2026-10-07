"""Token cost estimates from `config/pricing.yaml` (USD per 1M tokens)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


@lru_cache(maxsize=2)
def load_pricing(path: Path) -> dict[str, Any]:
    """Parsed pricing table."""
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data


def chat_cost(pricing: dict[str, Any], model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimated USD for one chat call (unknown model -> `default` prices)."""
    table = pricing["chat"]
    price = table.get(model) or next(
        (v for k, v in table.items() if k != "default" and model.startswith(k)), table["default"]
    )
    return (input_tokens * float(price["input"]) + output_tokens * float(price["output"])) / 1_000_000


def embedding_cost(pricing: dict[str, Any], model: str, tokens: int) -> float:
    """Estimated USD for embedding `tokens` tokens."""
    table = pricing["embedding"]
    return tokens * float(table.get(model, table["default"])) / 1_000_000
