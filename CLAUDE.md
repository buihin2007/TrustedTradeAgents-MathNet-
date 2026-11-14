# Trusted Trade Agents (Junction 2026, Valio Aimo track)

Read this file before writing any code. The full plan: https://claude.ai/artifact/BUTtVyQLjNt5hKhqRmXBZg

## What we build

A login app for a buyer (F&B manager) on top of Valio Aimo's MCP gateway.

- `web` (Next.js) -> `api` (FastAPI, buyer side) -> `nginx` -> `mcp` (FastMCP, Valio Aimo gateway) -> Supabase Postgres + Valio mock API.
- The purchasing agent is a fixed pipeline inside `api`. The LLM only parses the request and writes explanations. Code makes every decision.
- Every agent tool call passes the Gate in `mcp`: identity, mandate, caps, Jev, order policy. Outcomes: placed / held / blocked.

## Folders and owners

| Folder | Owner | Notes |
| --- | --- | --- |
| `packages/contracts/` | P1 | Shared Pydantic models. Do NOT edit unless assigned. |
| `supabase/migrations/` | P1 | Numbered `0001_*.sql`. Send SQL to P1, do not add files yourself. |
| `scripts/import/` | P1 | One-off data import |
| `services/mcp/search/` | P1 | Criteria -> guarded SQL -> hybrid rank |
| `services/mcp/` (rest) | P2 | Tools, Gate middleware, Jev, order policy, held orders |
| `services/api/` | P3 | Agent pipeline, audit log, REST for web |
| `apps/web/`, `infra/`, `docker-compose.yml`, `scripts/rogue_agent.py` | P4 | |
| Design | UI-UX | Delivers screens and tokens to P4, no code folder |

Only edit your own folders. Need a change elsewhere: ask its owner.

## Trust boundaries (never break these)

1. `api` never reads schema `wholesaler`. Everything goes through MCP tools.
2. `mcp` never trusts numbers from the agent. Prices, stock and totals are recomputed by the gateway.
3. User tools (`register_intent`, `approve_order`, `reject_order`, `get_mandate`, `update_mandate`, `set_agent_status`, `list_gateway_events`) run only with the user's Supabase JWT. Agent key -> `FORBIDDEN`. See `tta_contracts/tools.py`.
4. Jev can only make a decision stricter. Numeric limits are checked in code.
5. Generated SQL: one SELECT on `wholesaler.v_product_search` + `wholesaler.hybrid_rank`, named bind params only, LIMIT required, role `search_ro`, read-only transaction, 2 s timeout, rows re-checked against `must`.

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

- One branch per person: `p1p4-data-web`, `p2-gate`, `p3-api`.
- Merge to `main` via PR at each milestone: M0 0:45, M1 4:00, M2 8:00, M3 11:00.
- Between milestones: `git pull origin main` into your branch.
- `main` must always start with `docker compose up --build`.
- Never commit `.env` or `data/` (product JSON is 87 MB).
- Lock versions (`uv.lock`, `pnpm-lock.yaml`). No library upgrades mid-hackathon.

## Commands

```bash
docker compose up --build              # whole stack on http://localhost:8080
docker compose run --rm import         # one-off data import
uv run pytest                          # inside services/api or services/mcp
npx @modelcontextprotocol/inspector    # point at http://localhost:8080/mcp with the agent key
```

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
