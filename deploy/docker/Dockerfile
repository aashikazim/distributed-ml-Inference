FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
COPY src ./src
ARG INSTALL_ML=0
ARG TORCH_DEVICE=cpu
RUN if [ "$INSTALL_ML" = "1" ]; then if [ "$TORCH_DEVICE" = "cuda" ]; then uv sync --frozen --no-dev --no-editable --extra cuda; \
    else uv sync --frozen --no-dev --no-editable --extra ml; fi; \
    else uv sync --frozen --no-dev --no-editable; fi

FROM python:3.12-slim-bookworm AS runtime
RUN useradd --uid 10001 --create-home app && mkdir /models && chown app:app /models
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY config ./config
ENV PATH="/app/.venv/bin:$PATH"
USER 10001
CMD ["uvicorn", "infer_platform.gateway.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]

FROM runtime AS gateway
CMD ["uvicorn", "infer_platform.gateway.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]

FROM runtime AS worker
CMD ["python", "-m", "infer_platform.worker.main"]

FROM runtime AS autoscaler
CMD ["python", "-m", "infer_platform.autoscaler.main"]

FROM runtime AS exporter
CMD ["uvicorn", "infer_platform.exporter.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "9002", "--no-access-log"]

FROM runtime AS fake-openai
CMD ["uvicorn", "infer_platform.fake.openai_server:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]

FROM gateway AS default
