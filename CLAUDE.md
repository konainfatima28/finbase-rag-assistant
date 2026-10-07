# FinBase assistant — agent conventions

Production-grade RAG customer-support assistant over six FinBase policy PDFs. `PROMPT.md` is the
authoritative spec; `docs/REQUIREMENTS_CHECKLIST.md` tracks status; `docs/BUILD_LOG.md` holds evidence.

## Commands (cross-platform: `python scripts/tasks.py <task>`; `make <task>` is a thin wrapper)
| task | what |
|---|---|
| `setup` | create `.venv` (Python 3.11) + install `requirements-dev.txt` |
| `audit` | `python -m app.audit` → `docs/DATA_AUDIT.md` (Phase-0 gate; exit 1 on failure) |
| `check-openai` | verify `OPENAI_API_KEY` + configured models with a real API call |
| `ingest` | `python -m app.ingest` → `indexes/openai-<embed-model>/` (needs `OPENAI_API_KEY`) |
| `lint` | `ruff check app eval tests` + `ruff format --check` |
| `typecheck` | `mypy` (strict on `app/`) |
| `test` | `pytest --cov=app` (unit + integration; FakeLLM, no network) |
| `eval` | `python -m eval.run --provider … --judge …` ; `eval-retrieval` = offline retrieval-only |
| `dev-api` / `dev-web` | uvicorn on :8000 / Next.js on :3000 |
| live E2E | `cd web && E2E_LIVE_API=http://127.0.0.1:8000 npx playwright test e2e/live.spec.ts` (API must allow origin `http://127.0.0.1:3100`) |
| warm reranker | `python -m app.retrieval.warm` (build step on Render/Docker/CI) |

## Conventions
- Python 3.11, typed (`mypy --strict` on `app/`), `ruff` clean, structured logging (`structlog`) only — no `print` in `app/`.
- `pathlib` + `encoding="utf-8"` on every file open. No shell-only assumptions.
- No LangChain/LlamaIndex in the runtime path. Providers behind `app/providers/base.py` interfaces.
- **OpenAI only, for dev and prod (owner decision D-011). Never install/use Ollama or local models.**
  Real OpenAI calls need `OPENAI_API_KEY` in `.env`; fakes are allowed only inside `tests/`.
- Model names come from settings/env only — never hard-code them in logic.
- Never mix embedders: one index dir per `(provider, model)`; manifest is verified at startup.
- Never "repair" source values; never cite section numbers written inside answer text — citations
  come from chunk metadata (doc title + section + page + chunk_id).
- Mocks/FakeLLM only in `tests/`. No fake numbers in docs — every reported result must come from a run.
- After each phase: update `docs/BUILD_LOG.md`, `docs/REQUIREMENTS_CHECKLIST.md`, `docs/DECISIONS.md`.
