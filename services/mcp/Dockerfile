# Build context = repo root (see docker-compose.yml). Owner: P2.
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1
WORKDIR /repo/services/mcp
COPY packages/contracts /repo/packages/contracts
COPY services/mcp/pyproject.toml ./
RUN uv sync --no-dev
COPY services/mcp ./
EXPOSE 9000
CMD ["python", "-m", "app.server"]
