"""Application settings.

Precedence (highest first): explicit init kwargs > environment variables > `.env` > `config/settings.yaml`.
No retrieval/generation parameter is hard-coded in logic; everything is read from here.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT_DIR / "config"

#: Implemented providers. Owner decision D-011: OpenAI only (dev + prod); the abstraction stays extensible.
Provider = Literal["openai"]


class Settings(BaseSettings):
    """All runtime configuration. Field names map 1:1 to upper-case env vars."""

    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        yaml_file=CONFIG_DIR / "settings.yaml",
        yaml_file_encoding="utf-8",
    )

    # --- providers (OpenAI for local dev AND production — DECISIONS D-011) --------------------------
    llm_provider: Provider = "openai"
    embed_provider: Provider | None = None  # defaults to llm_provider
    openai_api_key: SecretStr | None = None
    openai_base_url: str | None = None
    openai_chat_model: str = "gpt-4.1-mini"
    openai_embed_model: str = "text-embedding-3-small"
    openai_rewrite_model: str | None = None  # defaults to openai_chat_model
    judge_provider: Provider | None = None
    judge_model: str | None = None  # defaults to openai_chat_model

    # --- generation -------------------------------------------------------------------------------
    temperature: float = 0.0
    seed: int = 42
    max_tokens: int = 500
    llm_timeout_s: float = 60.0
    llm_max_retries: int = 2
    rewrite_timeout_s: float = 15.0
    prompt_version: str = "v2"

    # --- ingestion --------------------------------------------------------------------------------
    chunk_max_tokens: int = 600
    chunk_target_tokens: int = 400
    chunk_overlap_ratio: float = 0.12
    embed_batch_size: int = 64

    # --- retrieval --------------------------------------------------------------------------------
    dense_k: int = 20
    bm25_k: int = 20
    rrf_k: int = 60
    rerank_top_n: int = 12
    final_k: int = 5
    context_token_budget: int = 2500
    boilerplate_weight: float = 0.6
    max_per_section: int = 2
    router_boost: float = 1.15
    faq_weight: float = 0.95
    reranker: Literal["flashrank", "none"] = "flashrank"
    reranker_model: str = "ms-marco-MiniLM-L-12-v2"
    # broad questions: up to `max_subqueries` sub-queries (from the rewrite call), merged before assembly
    max_subqueries: int = 3
    subquery_rerank_top_n: int = 8
    multi_query_final_k: int = 8
    multi_query_token_budget: int = 3800

    # --- api ----------------------------------------------------------------------------------
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    rate_limit: str = "20/minute"
    max_message_chars: int = 2000
    history_max_messages: int = 6
    history_max_chars: int = 6000
    sse_heartbeat_s: float = 15.0
    cache_ttl_s: int = 3600
    cache_max_items: int = 512
    session_ttl_s: int = 3600
    log_level: str = "INFO"
    port: int = 8000

    # --- paths (relative to repo root unless absolute) ---------------------------------------------
    data_dir: Path = Path("data")
    index_root: Path = Path("indexes")
    cache_dir: Path = Path(".cache")
    prompts_dir: Path = Path("prompts")
    eval_results_dir: Path = Path("eval/results")
    feedback_log_path: Path = Path("logs/feedback.jsonl")
    thresholds_path: Path = Path("config/thresholds.json")
    pricing_path: Path = Path("config/pricing.yaml")

    support_email: str = Field(default="support@finbase.com")
    support_helpline: str = Field(default="1800-FIN-BASE (1800-346-2273)")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Env beats .env beats YAML defaults."""
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            YamlConfigSettingsSource(settings_cls),
            file_secret_settings,
        )

    @field_validator(
        "embed_provider",
        "judge_provider",
        "openai_rewrite_model",
        "judge_model",
        "openai_base_url",
        mode="before",
    )
    @classmethod
    def _blank_is_none(cls, value: Any) -> Any:
        return None if value in ("", None) else value

    # --- derived ---------------------------------------------------------------------------------
    @property
    def effective_embed_provider(self) -> Provider:
        """Embedding provider (defaults to the chat provider)."""
        return self.embed_provider or self.llm_provider

    @property
    def embed_model(self) -> str:
        """Model name of the configured embedder."""
        return self.openai_embed_model

    @property
    def chat_model(self) -> str:
        """Model name of the configured chat provider."""
        return self.openai_chat_model

    @property
    def rewrite_model(self) -> str:
        """Model used for query rewriting/translation."""
        return self.openai_rewrite_model or self.openai_chat_model

    @property
    def effective_judge_model(self) -> str:
        """Model used as LLM-judge in evaluation."""
        return self.judge_model or self.openai_chat_model

    @property
    def has_openai_key(self) -> bool:
        """True if a non-empty OPENAI_API_KEY is configured (value never logged)."""
        return bool(self.openai_api_key and self.openai_api_key.get_secret_value().strip())

    @property
    def cors_origin_list(self) -> list[str]:
        """Exact CORS origins (never '*')."""
        return [o.strip().rstrip("/") for o in self.cors_origins.split(",") if o.strip() and o.strip() != "*"]

    def path(self, value: Path) -> Path:
        """Resolve a configured path against the repo root."""
        return value if value.is_absolute() else ROOT_DIR / value

    def index_dir_for(self, provider: str, model: str) -> Path:
        """Per-embedder index directory, e.g. `indexes/openai-text-embedding-3-small`."""
        return self.path(self.index_root) / index_dir_name(provider, model)

    @property
    def index_dir(self) -> Path:
        """Index directory of the currently configured embedder."""
        return self.index_dir_for(self.effective_embed_provider, self.embed_model)

    def load_thresholds(self) -> dict[str, Any]:
        """Calibrated thresholds (written by `python -m eval.calibrate`)."""
        path = self.path(self.thresholds_path)
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return data


def index_dir_name(provider: str, model: str) -> str:
    """Filesystem-safe directory name for an embedder."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", model).strip("-")
    return f"{provider}-{safe}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings singleton (immutable after creation)."""
    return Settings()
