# Deployment

Two services: the **API** on Render (Python, free tier) and the **UI** on Vercel (Next.js). The browser calls Render directly. SSE is not proxied through Vercel, because proxies buffer streams.

## 0. Prerequisites (once)

1. Create an OpenAI API key with billing enabled: https://platform.openai.com/api-keys
2. Build the index locally and commit it, so Render never calls OpenAI during its build:
   ```bash
   cp .env.example .env                  # set OPENAI_API_KEY
   python scripts/tasks.py setup         # or: make setup
   python -m app.providers.check         # verifies the key and models
   python -m app.ingest                  # -> indexes/openai-text-embedding-3-small/
   python -m eval.calibrate              # -> config/thresholds.json (calibrated)
   python -m eval.run --provider openai --judge openai   # -> eval/results/latest.json (dashboard)
   git add indexes/ config/thresholds.json eval/results/ eval/cache/ && git commit -m "Build OpenAI index"
   ```
   Never commit `.env`. It is git-ignored; check with `git check-ignore .env`.

## 1. API on Render

1. Push the repository to GitHub.
2. Render dashboard → **New + → Blueprint** → select the repo. Render reads `render.yaml`: Python 3.11.9, build `pip install -r requirements.txt && python -m app.retrieval.warm`, start `uvicorn app.main:app --host 0.0.0.0 --port $PORT …`, health check `/api/health`.
3. When prompted, set the two `sync: false` variables:
   - `OPENAI_API_KEY` = your key
   - `CORS_ORIGINS` = `https://<your-app>.vercel.app,http://localhost:3000` (add the Vercel URL after step 2 of the Vercel section, then redeploy)
4. Deploy and wait for "Live". Open `https://<service>.onrender.com/api/health`. You should see `"provider":"openai"`, `"chunks":190` and `"gate_calibrated":true`.
5. If startup fails with `IndexNotFoundError` or `IndexMismatchError`, the committed index is missing or does not match `OPENAI_EMBED_MODEL`. Rebuild it locally (step 0.2) and push.

**Free-tier caveats.** The service sleeps after ~15 min idle, and the first request then takes ~30–60 s (cold start). The UI shows an "API is waking up" banner, polls `/api/health` and resends automatically. Memory limit is 512 MB; the API avoids torch/transformers and loads PDF libraries only in the offline CLI. Measured locally with the real index: 168 MB RSS after startup and 205 MB after 40 real chat requests (Windows; see README). `render.yaml` keeps memory down with one worker (`--workers 1`), batch-4 reranking and single-threaded math libraries (D-034). It also uses fixed glibc thresholds (`MALLOC_MMAP_THRESHOLD_` / `MALLOC_TRIM_THRESHOLD_` = 131072, D-035), so the reranker's large transient tensors are returned to the OS instead of being retained by the allocator. If you create the service manually instead of as a Blueprint, set these environment variables and the start command yourself.

## 2. UI on Vercel

1. Vercel → **Add New → Project** → import the repo → **Root Directory: `web`** (framework Next.js is auto-detected; Node 22 from `.nvmrc`).
2. Environment variable: `NEXT_PUBLIC_API_URL` = `https://<service>.onrender.com` (no trailing slash). It is public by design and contains no secret.
3. Deploy, then copy the Vercel URL into Render's `CORS_ORIGINS` and redeploy the API.

## 3. Post-deploy smoke test

```bash
scripts/smoke_test.sh https://<service>.onrender.com
```
```powershell
./scripts/smoke_test.ps1 -Api https://<service>.onrender.com
```
The script checks `/api/health` (waits up to 90 s for a cold start), one answerable question (must be `answerable` with sources), one not-in-KB question (must abstain), and the SSE stream (`meta`, `token` and `done` events). Then open the Vercel URL, ask a starter question, click a citation chip, and open `/eval`.

## 4. Docker (local full stack)

```bash
cp .env.example .env   # OPENAI_API_KEY
docker compose up --build
# web http://localhost:3000   api http://localhost:8000/docs
```
`backend/Dockerfile` (build context = repo root) installs the runtime requirements, bakes in the FlashRank model, copies the committed index and runs as a non-root user. The key is passed only at runtime via `env_file`, so it is in no image layer (`.dockerignore` excludes `.env`). `web/Dockerfile` builds the Next.js standalone server, with `NEXT_PUBLIC_API_URL` as a build arg.

## 5. Environment variables

See the table in the README (every variable, default, required, description) and `.env.example`.

## Troubleshooting

| symptom | cause / fix |
|---|---|
| UI banner "API URL is not configured" | `NEXT_PUBLIC_API_URL` was not set at build time → set it in Vercel and redeploy |
| Browser console CORS error | Vercel origin missing from `CORS_ORIGINS` (exact match, no trailing slash) |
| `/api/health` 502 for ~1 min | Render cold start; wait or use the waking banner |
| `IndexMismatchError` at startup | `OPENAI_EMBED_MODEL` differs from the committed index → rebuild with `python -m app.ingest` |
| Answers say "temporarily unavailable" | OpenAI error (invalid key, quota, outage) → check Render logs (`llm_unavailable`) and `python -m app.providers.check` |
| 429 responses | per-IP rate limit (`RATE_LIMIT`, default 20/minute) |
