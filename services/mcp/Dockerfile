# Build context is the monorepo root.
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.10.9 /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1
WORKDIR /repo/services/mcp
COPY packages/contracts /repo/packages/contracts
COPY services/mcp/pyproject.toml services/mcp/uv.lock ./
RUN uv sync --locked --no-dev
COPY services/mcp/app ./app
COPY services/mcp/search ./search
USER 65534:65534
EXPOSE 9000
CMD ["python", "-m", "app.server"]
