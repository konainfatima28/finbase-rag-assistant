# FinBase API image. Build context = repo root:  docker build -f backend/Dockerfile .
# No secrets in any layer: OPENAI_API_KEY is provided only at runtime (env / --env-file).
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv
RUN useradd --create-home --uid 10001 appuser

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY app/ app/
COPY config/ config/
COPY prompts/ prompts/
COPY data/sources.yaml data/sources.yaml
COPY indexes/ indexes/
COPY eval/results/ eval/results/

# Bake the reranker model into the image (no download on cold start), then drop privileges.
RUN python -m app.retrieval.warm && mkdir -p logs && chown -R appuser:appuser /srv/logs /srv/.cache
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s CMD python -c "import os,urllib.request;urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/api/health',timeout=4)"
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --proxy-headers --forwarded-allow-ips='*' --no-access-log"]
