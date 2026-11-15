# Trusted Trade Agents (Junction 2026, Valio Aimo track)

Read this file before writing any code. Full plan: `docs/PLAN.md` (live version: https://claude.ai/artifact/BUTtVyQLjNt5hKhqRmXBZg).

## What we build

A login app for a buyer (F&B manager) on top of Valio Aimo's MCP gateway.

- `web` (Next.js) -> `api` (FastAPI, buyer side) -> `nginx` -> `mcp` (FastMCP, Valio Aimo gateway) -> Supabase Postgres + Valio mock API.
- The purchasing agent is a fixed pipeline inside `api`. The LLM only parses the request and writes explanations. Code makes every decision.
- Buyer agent (`services/api/app/agent.py`): one LLM call understands the request (products or menu) while Jev screens it; code asks one multiple-choice question at a time (people, date, quantity, dishes, basket); menus get recipes from the LLM, checked against Valio stock; sourcing is `register_intent` (user JWT) + `search_products_batch` + `get_price_availability`; baskets are computed in code; the buyer picks one and the gateway decides placed / held / blocked.
- The agent publishes every step to the browser over `GET /api/requests/{id}/stream` (SSE, whole-snapshot events); the state lives in `buyer.requests.parsed` (`feed` of steps, questions, dish cards, baskets).
- Jev screens the buyer's first message in `api` (spam / small talk -> request rejected, no LLM parse, no gateway call). Fails open if Jev is down.
- Every agent tool call passes the Gate in `mcp`: identity, mandate, caps, order policy. Outcomes: placed / held / blocked. Per-call Jev in the gate is opt-in (`GATEWAY_JEV=1`).

## Folders and owners

| Folder | Owner | Notes |
| --- | --- | --- |
| `packages/contracts/` | P1 | Shared Pydantic models. Do NOT edit unless assigned. |
| `supabase/migrations/` | P1 | Numbered `0001_*.sql`. Send SQL to P1, do not add files yourself. |
| `scripts/import/` | P1 | One-off data import |
| `services/mcp/search/` | P1 | Criteria -> guarded SQL -> hybrid rank |
| `services/mcp/` (rest) | P2 | Tools, Gate middleware, Jev, order policy, held orders |
| `services/api/` | P3 | Agent pipeline, audit log, REST for web |
| `apps/web/`, `infra/nginx/`, `docker-compose.yml`, `scripts/rogue_agent.py` | P4 | |
| `infra/grafana/`, `scripts/demo/` | UI-UX (or P2) | Valio Aimo audit dashboard |
| Design | UI-UX | Delivers screens and tokens to P4, no code folder |

Only edit your own folders. Need a change elsewhere: ask its owner.

## Trust boundaries (never break these)

1. `api` never reads schema `wholesaler`. Everything goes through MCP tools.
2. `mcp` never trusts numbers from the agent. Prices, stock and totals are recomputed by the gateway.
3. User tools (`register_intent`, `approve_order`, `reject_order`, `get_mandate`, `update_mandate`, `set_agent_status`, `list_gateway_events`) run only with the user's Supabase JWT. Agent key -> `FORBIDDEN`. See `tta_contracts/tools.py`.
4. Jev only filters what reaches the agent; it never approves anything. Numeric limits are checked in code.
5. Generated SQL: one SELECT on `wholesaler.v_product_search` + `wholesaler.hybrid_rank`, named bind params only, LIMIT required, role `search_ro`, read-only transaction, 2 s timeout, rows re-checked against `must`.

## Service rules

- Every service has a `Dockerfile` at its folder root, built with the repo root as context.
- Every service answers `GET /health` with 200. Compose and nginx depend on it.
- Ports: web 3000, api 8000, mcp 9000. Only nginx is published (8080).
- `api` exposes public routes under `/api/...`. `mcp` serves MCP at `/mcp` (no trailing slash).
- `api` calls the gateway at `MCP_URL=http://nginx/mcp`, never `mcp:9000`, so rate limits apply.
- Database: migrations `0001_init.sql`, `0002_audit_dashboard.sql`, `0003_import_fixes.sql` (run in order), demo data `supabase/seed.sql`, fake gateway traffic `scripts/demo/fake_gateway_events.sql`.
- Models: embeddings `baai/bge-m3` = `vector(1024)` (same model at import and at query time); LLM `deepseek/deepseek-v4.1-flash` with fallback `anthropic/claude-haiku-5.5`. Call OpenRouter with `"models": [LLM_MODEL, LLM_MODEL_FALLBACK]`, `response_format` json_schema strict, `"provider": {"sort": "price", "require_parameters": true}`, then validate with Pydantic.
- Tracing: call `tta_contracts.telemetry.langfuse_otel_env()` at startup; it builds the OTLP settings from `LANGFUSE_*`. Export with OpenTelemetry, not the Langfuse SDK (the SDK drops non-LLM spans by default).
- Grafana (Valio Aimo audit dashboard) runs on http://localhost:3001, outside nginx. It reads only the `wholesaler.v_audit_*` views as role `dashboard_ro` (migration 0002). Never point it at base tables.
- Every gate decision, including Gate-level rate limits (`RATE_LIMIT`), must write one row to `wholesaler.gateway_events`, or the dashboard cannot show it. Requests rejected with 429 by nginx never reach the gate.
- Connections: Supabase session pooler allows 15 clients per role by default, shared by the whole team. Keep each service's DB pool at 2 connections max.
- Generated SQL runs as: `BEGIN READ ONLY; SET LOCAL ROLE search_ro; SET LOCAL statement_timeout = '2s'; ...`.

## Web app (apps/web)

- Next.js 16 App Router, `@supabase/ssr`. `proxy.ts` refreshes the session and redirects signed-out users to `/login`; server code verifies with `getClaims()`, never `getSession()`.
- The browser calls the buyer API on the same origin (`/api/...` through nginx) with `Authorization: Bearer <Supabase access token>` (see `lib/api.ts`).
- `apps/web/lib/types.ts` is the JSON contract between web and `services/api`: P3 returns exactly these fields.
- `NEXT_PUBLIC_*` values are baked in at build time: after changing them run `docker compose up -d --build web`.
- Local dev without docker: `cd apps/web && npm install && API_PROXY_URL=http://localhost:8080 npm run dev` (needs the `NEXT_PUBLIC_*` vars in `apps/web/.env.local`).
- Status colours mean the same everywhere: blue `auto` = automatic, orange `human` = needs a person, black `blocked`.

## Contracts

`packages/contracts` is the single source of truth for every request and response between `api` and `mcp`.

- Tool inputs and outputs = models in `tta_contracts` (e.g. `PlaceOrderRequest` -> `Order`).
- Reason codes = `ReasonCode`. Audit decisions = `AuditDecision`. Do not invent new strings.
- Changing a contract: tell the whole team, P1 merges it to `main`, everyone runs `git pull origin main`.

Install in a service (Python 3.12, uv):

```bash
cd services/api   # or services/mcp
uv add --editable ../../packages/contracts
```

## Git

- One branch per person: `db-hin` (P1 + P4), `mcp-hoang` (P2), `api-chu` (P3).
- Merge to `main` via PR at each milestone: M0 0:45, M1 4:00, M2 8:00, M3 11:00.
- Between milestones: `git pull origin main` into your branch.
- `main` must always start with `docker compose up --build`.
- Never commit `.env` or `data/` (product JSON is 87 MB).
- Lock versions (`uv.lock`, `apps/web/package-lock.json`). No library upgrades mid-hackathon.

## Commands

```bash
docker compose up --build              # buyer app on http://localhost:8080, audit dashboard on http://localhost:3001
python scripts/import/run.py            # one-off data import (from repo root, needs .env); --limit 200 to test
uv run pytest                          # inside services/api or services/mcp
npx @modelcontextprotocol/inspector    # point at http://localhost:8080/mcp with the agent key
```

## Data facts (checked against the real files)

- Product ids are strings in the source and in the Valio API (`"460013"`). Store and pass them as `int` internally; send `[str(p) for p in ids]` to `/api/price` and `/api/availability`, read `int(productId)` back.
- Valio API body: `{"customer": VALIO_CUSTOMER, "products": [...], "requestedDate": "YYYY-MM-DD"}`, header `X-API-Key`.
- `allowedLotSize` is fractional (0.01 for 16,872 of 17,546 products), so order quantities are floats.
- Allergens: `allergens_contains` holds codes declared CONTAINS or MAY_CONTAIN (GS1 codes, AM = milk, AW = gluten cereals, AE = eggs).
- Jev answers look like `{"type": "noul", "noul": 0.93}`: read `answers[q]["noul"]`.
- Unit sizes are computed per sales unit; about 18 source records had grams stored as kg (fixed at import against the product name).

## Read the docs before using a library (do not guess APIs)

- FastMCP: https://gofastmcp.com/docs/llms.txt (middleware: on_call_tool + ToolError; telemetry: fastmcp.telemetry.get_tracer)
- Jev gate pattern: https://openrouter.ai/docs/cookbook/building-agents/gate-tool-calls-with-jev
- OpenRouter embeddings: https://openrouter.ai/docs/api/reference/embeddings
- Supabase hybrid search: https://supabase.com/docs/guides/ai/hybrid-search
- Supabase Next.js auth (Next 16 uses proxy.ts, verify with getClaims): https://supabase.com/docs/guides/auth/server-side/nextjs
- Supabase connection (use Session pooler, port 5432): https://supabase.com/docs/guides/database/connecting-to-postgres
- Langfuse OTLP (HTTP only, no gRPC): https://langfuse.com/integrations/native/opentelemetry

## Definition of done for any task

- Code runs inside `docker compose up`.
- Uses models from `tta_contracts`, no duplicated schemas.
- A test or a manual check command is written in the PR description.
