FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
COPY src ./src
ARG INSTALL_ML=0
RUN if [ "$INSTALL_ML" = "1" ]; then uv sync --frozen --no-dev --no-editable --extra ml; \
    else uv sync --frozen --no-dev --no-editable; fi

FROM python:3.12-slim-bookworm AS runtime
RUN useradd --uid 10001 --create-home app && mkdir /models && chown app:app /models
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY config ./config
ENV PATH="/app/.venv/bin:$PATH"
USER 10001
CMD ["uvicorn", "infer_platform.gateway.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
