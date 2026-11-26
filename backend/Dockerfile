# syntax=docker/dockerfile:1.7
ARG PYTHON_IMAGE=python:3.12.12-slim-trixie
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.13.7

FROM ${UV_IMAGE} AS uv

# Dependencies only, exactly as locked. No dev tools reach the runtime image.
FROM ${PYTHON_IMAGE} AS builder
COPY --from=uv /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /build
COPY pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

# The embedding model is baked in at build time so a container never downloads at boot.
# The Retrieval chat owns the choice of model; override with --build-arg EMBEDDING_MODEL=...
FROM builder AS model
ARG EMBEDDING_MODEL=BAAI/bge-small-en-v1.5
ENV FASTEMBED_CACHE_PATH=/opt/models \
    ORT_DISABLE_TELEMETRY=1
# a+rX: hub writes some cache files 0600; the non-root runtime user must read all of them.
RUN /opt/venv/bin/python -c "import os; from fastembed import TextEmbedding; TextEmbedding(model_name=os.environ['EMBEDDING_MODEL'], cache_dir=os.environ['FASTEMBED_CACHE_PATH'])" \
 && chmod -R a+rX,go-w /opt/models

FROM ${PYTHON_IMAGE} AS runtime
ARG EMBEDDING_MODEL=BAAI/bge-small-en-v1.5
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --no-create-home --home-dir /nonexistent \
      --shell /usr/sbin/nologin app
# HF_HUB_OFFLINE: a missing model must fail loudly, never trigger a download in production.
# ORT_DISABLE_TELEMETRY: official onnxruntime builds send usage telemetry unless told not to.
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    EMBEDDING_MODEL=${EMBEDDING_MODEL} \
    FASTEMBED_CACHE_PATH=/opt/models \
    HF_HUB_OFFLINE=1 \
    ORT_DISABLE_TELEMETRY=1
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --from=model /opt/models /opt/models
COPY alembic.ini ./
COPY app ./app
# Code and model stay root-owned: the process can read them but never modify them.
USER app
EXPOSE 8000
# No --proxy-headers: X-Forwarded-For is not trusted; the client IP comes from X-Client-IP,
# which only the proxy can set (it must also pass the X-Proxy-Secret gate).
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", \
     "--no-server-header", "--timeout-graceful-shutdown", "20"]
