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

## Phase 1: fixed pipeline and REST integration

`POST /api/requests` accepts `{"text":"40 litres of milk ..."}` and returns a
request with HTTP 202. Poll `GET /api/requests/{id}` for status/step, questions,
options and the authoritative gateway order. `GET /api/requests?limit=50&offset=0`
lists the current buyer's requests. OpenAPI is available at `/openapi.json` when
running the API directly on port 8000 (nginx only forwards `/api/...`).

The buyer profile must be seeded in `buyer.profiles`. Queries scope access to
both authenticated user and organisation; this prototype does not give every
organisation member access to another member's requests. The DB pool has at most
two connections. No API query reads `wholesaler`.

Pipeline: facets -> parse -> register intent with user JWT -> search -> quote ->
build options -> explain -> place order with agent key. Facets precede intent
registration because the shared `RegisterIntentRequest` already requires coded
criteria. This resolves the ordering ambiguity in PLAN; coordinate with P1/P2.

The gateway adapter reads tool input schemas and supports flattened parameters
or a single `request: SharedModel` parameter. Other signature changes require
coordination with P2. The current MCP scaffold only has `ping`, so a real purchase
requires P1/P2 to provide the documented tools.

Choices are deterministic: relevance (brand breaks ties), unit price, reliability
(CO2 breaks ties), then product ID for stable ties. Identical baskets are shown
once. `pcs` means sales units; carton contents are not inferred. Unknown price,
stock or sales-unit size cannot become a proposal. Splits must fulfil the complete
quantity, respect lot sizes and fit the contract's 10-line cap. These explicit
buyer-side choices need P1/P4 review; they do not change gateway policy.

`DATABASE_URL`, `MCP_URL`, `AGENT_KEY`, `OPENROUTER_API_KEY` and `LLM_MODEL` configure
the pipeline. Use an OpenRouter model supporting JSON Schema structured outputs.
Order submission includes quote ID and a stable request/intent idempotency key.
Policy denials are never retried; read transport failures get up to 3 attempts;
order transport retries require an idempotency key. User approval is not retried.

Validation: `uv run pytest -q` exercises real Supabase verification with local
JWKS, a real in-process FastMCP client/server round trip, mocked OpenRouter,
quantity/lot/ranking rules, pipeline outcomes and REST ownership checks. External
credentials are not used. Docker/Postgres/live-gateway validation is separate.
