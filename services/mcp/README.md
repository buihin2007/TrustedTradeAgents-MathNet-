# Valio Aimo MCP gateway

The gateway implements the MCP scope of [the project plan](../../docs/PLAN.md). It serves Streamable HTTP at `/mcp` on port 9000 and exposes `GET /health`. Buyer applications use `http://nginx/mcp` inside Compose and local MCP Inspector uses `http://localhost:8080/mcp`. Use a separate MCP client for each bearer identity.

## Integration

| Caller | Tools |
| --- | --- |
| Agent with a `tta_...` bearer key | `get_facets`, `search_products`, `get_price_availability`, `get_substitutes`, `place_order`, `get_order` |
| Supabase JWT from the buyer | `register_intent`, `approve_order`, `reject_order`, `get_mandate`, `update_mandate`, `set_agent_status`, `list_gateway_events`, `get_order` |

The buyer forwards its Supabase access token when registering the original request. It passes the returned `intent_id` to its agent. The agent searches, quotes products for the intent's delivery date, then sends the quote ID and order options with a stable idempotency key. Order inputs carry product IDs and sales-unit quantities. Valio supplies prices and stock. The gateway applies the active mandate, checks the preferred option, and returns `placed`, `held`, or `blocked`. A placed order is recorded in Postgres because the challenge API only provides quotes and stock.

Most tools that have a shared `*Request` model take MCP arguments of the form `{"request": {...}}`. `approve_order` takes `order_id` and `option_index` as top-level fields. The remaining arguments are visible in `tools/list`. `update_mandate` takes `agent_id` and the top-level `MandateLimits` fields. `list_gateway_events` accepts `limit` and `before_id`. When approving a held order after `PRICE_MOVED`, render the refreshed option and pass its displayed `total_eur` as the top-level `expected_total_eur` argument after the signer confirms. The existing rejection request accepts a note. The shared schema stores the rejection decision and signer and has no field for the note.

The gateway authenticates against the wholesaler's agent and membership tables. Supabase JWTs use project JWKS, with Auth server verification for legacy HS256 projects. Mutations require the organization signer role. A buyer token must resolve to one wholesaler organization because the shared tool requests have no organization selector. Jev evaluates intent-scoped reads and orders. An unavailable Jev response flags reads and holds orders for review. The gateway enforces 60 calls per minute per agent in addition to nginx's burst limit and the distinct-product intent cap.

Catalog search combines the database's hybrid rank with structured filters and checks the returned rows against the registered intent and mandate. SQL generation is bounded to the audited query structure for each criteria shape. The validated statement runs as `search_ro` with a two-second timeout. Search can use full-text ranking if embeddings are unavailable. The plan lists trigram ranking and Jev reranking as follow-up ranking enhancements.

Postgres stores an event for each tool call that reaches the gate and has a working database connection. Grafana reads the audit views provided by migration 0002. FastMCP propagates trace context from the buyer client. Configure the OTLP endpoint and headers to export gateway spans to Langfuse.

## Run

Apply the repository's wholesaler migrations and seed, then load the catalog through the team's importer. Configure `.env` using the root example. MCP uses `DATABASE_URL`, `SUPABASE_URL`, the OpenRouter settings, and the Valio base URL, key and customer. Configure the Supabase publishable key for legacy JWT verification. The Postgres user needs the wholesaler grants and permission to assume `search_ro`. Use TLS for external connections.

From the repository root, run `docker compose up --build mcp`. To access MCP through nginx, start `nginx` and its Compose dependencies as well. For local development, set the same environment variables in your shell and run these commands from `services/mcp`.

```sh
uv sync --locked
uv run --locked python -m app.server
uv run --locked pytest -q
uv run --locked ruff check app search tests
```

The HTTP integration tests require `MCP_TEST_DATABASE_URL` pointing to a disposable Postgres instance with pgvector and privileges to create databases and roles. The suite creates uniquely named databases and applies the repository migrations. It drops those databases at teardown. `MCP_TEST_FTS_ONLY=1` runs against plain Postgres with a test-only adjustment to the vector type and distance expression. This mode exercises actual full-text SQL, the search role, and the gateway transactions.
