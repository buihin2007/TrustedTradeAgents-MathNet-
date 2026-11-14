# Buyer API (P3)

Python 3.12+, FastAPI. All buyer implementation lives in this directory.
Shared gateway contracts belong to P1; gateway tools/policy belong to P2.

## Development and tests

From `services/api`, with Python and uv installed:

```powershell
uv sync --locked
uv run pytest -q
uv run ruff check app tests
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Tests use locally generated signing keys and a mock JWKS endpoint. No Supabase
account, OpenRouter credits or external gateway is required for unit tests.

For the Docker stack, configure the root `.env` from `.env.example`, then run
`docker compose up --build` from the repository root. The API is reached at
`http://localhost:8080/api/health`; the frontend is owned by P4.

## Authentication

Health endpoints are public. Business routes require
`Authorization: Bearer <Supabase user access token>`. The API calls
`supabase.auth.get_claims(jwt=...)`, checks issuer, audience, expiry and user role,
and never accepts an agent key as a user. Supabase URL and public key must be set:

- `SUPABASE_URL`
- `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` (or `SUPABASE_PUBLISHABLE_KEY`)

Credentials are loaded from process environment (Compose supplies the root
`.env`). Missing configuration leaves health available but business operations
fail closed. JWTs/keys must not be logged, stored in database, or returned in JSON.

References: [Supabase get_claims](https://supabase.com/docs/reference/python/auth-getclaims),
[FastMCP bearer auth](https://gofastmcp.com/docs/clients/auth/bearer).
