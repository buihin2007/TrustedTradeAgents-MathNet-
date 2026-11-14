# Buyer API (P3)

Python 3.12+, FastAPI. All buyer implementation lives in this directory.
Shared gateway contracts belong to P1; gateway tools/policy belong to P2.

## Development and tests

From `services/api`, with Python and uv installed:

```powershell
uv sync --locked
uv run pytest -q
uv run ruff check app tests
uv run python -m app
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

## Phase 2: questions, approvals, audit and traces

- `POST /api/requests/{id}/answer` accepts
  `{"question_id":"<uuid>","answer":"40 litres"}`. Only an unanswered question
  on an owned `asking` request can advance the pipeline. PostgreSQL row locking
  prevents two submissions from starting two continuations. A continuation
  reparses the original text plus all recorded Q&A and registers a fresh intent.
  It does not silently change the original hard constraints.
- `GET /api/approvals` lists held orders referenced by the current user's buyer
  requests and refreshes them through `get_order`. It cannot list orders created
  independently by another client: there is no shared list-orders tool yet.
- `POST /api/approvals/{order_id}` accepts
  `{"action":"approve","option_index":0}` or
  `{"action":"reject","note":"..."}`. Both use the user's JWT. Repricing can
  return `held` again, which must be displayed for a new review.
  When `PRICE_MOVED` is shown, confirm the displayed option total explicitly:
  `{"action":"approve","option_index":0,"expected_total_eur":"420.00"}`.
  This is forwarded as the top-level argument supported by P2's current tool.
- `GET/PUT /api/mandate` use `Mandate`/`MandateLimits`; `POST /api/agent/pause`
  and `/resume` derive the agent ID from the authenticated profile.
- `GET /api/audit?request_id=<uuid>&decision=blocked&limit=50&offset=0` is scoped
  to this buyer. SQL writes serialize the global hash-chain head with an advisory
  transaction lock. The database's append-only trigger comes from P1's migration.
  A filtered/paginated audit response is not the full global chain and cannot be
  passed directly to `verify_chain` as if it started at the chain's first event.

Tracing generates request/pipeline/step spans and propagates W3C `traceparent`
into MCP metadata. LLM spans contain model/token counts. No keys, JWTs or full
prompts are added as span attributes. Set `OTEL_EXPORTER_OTLP_ENDPOINT` and
`OTEL_EXPORTER_OTLP_HEADERS` to export to Langfuse over HTTP; without both,
trace IDs still exist locally and nothing is sent. Coordinate deployment/exporter
settings with P4. `BUYER_TIMEZONE` defaults to `Europe/Kyiv` for relative-date
parsing; ambiguous dates remain a question for the buyer.

Local Windows startup uses `python -m app` to select the event loop required by
psycopg. Docker runs Linux and keeps the existing uvicorn entrypoint.

### Real PostgreSQL tests

With Docker's Linux engine running, start a disposable local database:

```powershell
docker run -d --name tta-api-test-db -e POSTGRES_USER=tta_test -e POSTGRES_PASSWORD=tta_test -e POSTGRES_DB=tta_api_test_local -p 127.0.0.1:55432:5432 postgres:17
$env:TEST_DATABASE_URL='postgresql://tta_test:tta_test@127.0.0.1:55432/tta_api_test_local'
uv run pytest -q
```

The database tests create a uniquely named local test database, apply the buyer
section of P1's actual migration, and drop only that created database at teardown.
They check concurrent audit appends, the append-only trigger, concurrent answers,
ownership filtering, real JSON/model round trips and transactional rollback.
Without `TEST_DATABASE_URL`, these nine tests are explicitly skipped. The tests
reject non-local hosts and database names outside `tta_api_test_*`.

Historical Phase 2 checkpoint before restart: 92 tests passed; five real-Postgres tests were
not run because Docker's engine returned HTTP 500. This is not a live integration
pass. Unit tests do not prove the P2 gateway or team Supabase setup is ready.

## Phase 3: failure handling and verified local integration

After the Docker/WSL restart, all five original DB tests passed. The expanded
suite now has **137 passing tests, including nine real-PostgreSQL tests**, on both
Windows/Python 3.12 and Linux/Python 3.12 in the API image. Ruff lint and format
checks also pass. The REST/pipeline/DB cases cover `placed`, `held` and `blocked`;
OpenRouter and purchasing responses remain test doubles, not live upstream calls.

- HTTP 401/403, invalid tool arguments and policy denials are not retried.
  Transient read failures get bounded retries. Order retries keep the same
  idempotency key; approval/rejection calls are never automatically repeated.
  HTTP `Retry-After` is respected; a long server-requested wait becomes an error
  instead of an early retry. Replaced MCP sessions are closed before reconnecting.
- Order state/options and the final audit event are saved in one transaction.
  If local persistence fails after a confirmed gateway result, the request keeps
  that known outcome and uses `step=sync_required`. Polling its detail refreshes
  the order through `get_order` and reconciles state/audit without placing again.
- A submission timeout with no confirmed result uses `step=submission_unknown`
  and explicitly asks the buyer to check order status before starting another
  request. This prototype does not automatically resubmit that pipeline after a
  restart. Graceful shutdown cancels/joins background tasks and records an
  interruption where the database is available.
- Tests cover grouped transport failures, connection timeouts, unchanged keys,
  reconnecting an agent while its user session remains usable, atomic rollback,
  post-submission audit failure and shutdown cancellation.
- When no single original product is sufficient, the API can request mapped
  substitutes for at most 10 originals. It obtains one fresh quote for at most
  50 unique product IDs, marks replacement lines with `substitute_for`, and
  retains a feasible original split as another choice. The gateway decides
  whether substitution is permitted and whether human approval is needed.
- Duplicate search hits cannot double-count stock. Agent pause/resume is reported
  as successful only when the gateway confirms the requested agent and status.

### Compose smoke check without team credentials

From the repository root:

```powershell
docker compose -p tta-p3-smoke -f docker-compose.yml -f services/api/compose.smoke.yml up -d --build --wait api mcp nginx
cd services/api
uv run python tests/smoke_stack.py
```

This verified nginx -> API health (200), unauthenticated purchase rejection (401)
and a real HTTP MCP discovery/ping at `http://127.0.0.1:18080`. The override clears
credential env files for this isolated project and leaves the P4 root Compose file
unchanged. It uses the real P2 scaffold, which currently only exposes `ping`;
it does not demonstrate a live purchase or the frontend.

From the repository root, stop only that smoke project with:

```powershell
docker compose -p tta-p3-smoke -f docker-compose.yml -f services/api/compose.smoke.yml down
```

`Dockerfile.dockerignore` excludes local Windows runtimes, virtual environments
and staged-check snapshots from the Linux build context. It follows Docker's
[Dockerfile-specific ignore convention](https://docs.docker.com/build/concepts/context/#filename-and-location).
The smoke override uses Compose's documented
[reset/override merge rules](https://docs.docker.com/reference/compose-file/merge/).

## P3 handoff

The buyer backend implementation for phases 0-3 is present in `services/api`:

| Phase | Delivered |
| --- | --- |
| 0 | FastAPI, public health, verified Supabase user JWTs, locked dependencies and Docker build |
| 1 | Buyer persistence, typed MCP sessions, structured request parsing, fixed purchase pipeline and REST endpoints |
| 2 | Clarification questions, deterministic options, explanations, approvals, mandates, agent controls, hash-chained audit and traces |
| 3 | Bounded retries, submission uncertainty, atomic order/audit writes, reconciliation, shortage/substitution flow and regression/integration tests |

Final review checked the tool signatures in `origin/mcp-hoang` at `0c69d41`.
The nested request models, flat mandate arguments, typed agent-status result and
`approve_order.expected_total_eur` are supported. That branch was inspected,
not merged into `api-chu`; `main` currently still contains the `ping` scaffold.

Remaining joint acceptance checks require P1/P2 tools merged into the integration
branch, live Supabase/Auth, OpenRouter credentials and the P4 frontend. The live
purchase, Langfuse ingestion and full-team demo have not been certified by these
local tests. P3's local code/test delivery is distinct from those team milestones.
P4 should generate REST types from OpenAPI and present a new confirmation when
the gateway returns a changed price. Shared contracts, migrations and other
owners' source folders were not modified by P3.
