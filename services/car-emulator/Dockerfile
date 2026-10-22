FROM ghcr.io/astral-sh/uv:0.13.4 AS uv

FROM python:3.14.7-slim AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Dependencies first, so code changes do not invalidate the cached dependency layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

FROM python:3.14.7-slim
RUN useradd --system --uid 10001 --no-create-home emulator
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    EMULATOR_HOST=0.0.0.0 \
    EMULATOR_PORT=8100
USER emulator
EXPOSE 8100
CMD ["car-emulator"]
