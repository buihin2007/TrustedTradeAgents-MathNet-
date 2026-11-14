# Build context = repo root (see docker-compose.yml). Owner: P3.
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1
WORKDIR /repo/services/api
COPY packages/contracts /repo/packages/contracts
COPY services/api/pyproject.toml ./
RUN uv sync --no-dev
COPY services/api ./
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
