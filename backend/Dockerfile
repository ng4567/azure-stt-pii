# Backend image: the benchmark API plus everything `data/stt.py` needs to talk to
# Azure Speech.
FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/usr/local

# ffmpeg     - normalizes uploaded audio to 16-bit mono/stereo PCM
# azure-cli  - backs DefaultAzureCredential from the mounted ~/.azure profile
# libasound2/libssl3 - runtime dependencies of the Speech SDK
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        azure-cli \
        libasound2t64 \
        libssl3 \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.9.9 /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY backend ./backend
COPY data/stt.py data/conversation.py data/system_prompt.txt ./data/
# The mock call is seeded as the default benchmark input on startup.
COPY data/mock-call.wav data/mock-call-stereo.wav data/mock-call-transcript.txt \
    data/mock-call-stereo-stt-benchmark-results.json ./data/

ENV REPO_ROOT=/app \
    BENCHMARK_DIR=/app/data \
    UPLOAD_DIR=/app/var/uploads

EXPOSE 8000

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
