# Integrating a purchasing agent with the Valio Aimo gateway

Any agent that speaks the [Model Context Protocol](https://modelcontextprotocol.io) can buy from Valio Aimo through this gateway: our own buyer app is just one client. This page is the contract an external agent needs.

| | |
| --- | --- |
| Endpoint | `POST /mcp`, MCP Streamable HTTP (stateless) |
| Public key for quote signatures | `GET /.well-known/jwks.json` |
| Tool schemas | `tools/list` on the endpoint (JSON Schema generated from `packages/contracts`) |
| Reference client | `services/api/app/gateway.py`; misbehaving client: `scripts/rogue_agent.py` |

## Identities

Every call carries `Authorization: Bearer <token>`. The gateway tells two kinds apart:

| Token | Who | May call |
| --- | --- | --- |
| Agent key, starts with `tta_` (Valio Aimo stores only its SHA-256) | The purchasing agent of one buyer organisation | Catalog, price, substitutes, `place_order`, `get_order` |
| Supabase JWT of a person who signed the organisation's mandate | The human buyer | `register_intent`, `approve_order`, `reject_order`, mandate and agent controls, the gateway log |

An agent can therefore never widen what it was asked to buy, approve its own order or raise its own limits. A wrong identity gets `FORBIDDEN` before any argument is read.

## Flow

```text
person (JWT)  register_intent(request_text, criteria, quantity, unit, delivery_date)  -> intent_id   valid 24 h
agent (key)   get_facets                                    -> claim, allergen, category codes
agent         search_products_batch(intent_id, queries[])   -> one result per query    (or search_products)
agent         get_price_availability(intent_id, ids[], day) -> signed quote, valid 15 min
agent         get_substitutes(intent_id, ids[])             -> Valio Aimo's own substitute pairs (optional)
agent         place_order(intent_id, quote_id, options[1-4], preferred_index, idempotency_key)
                                                             -> placed | held | blocked, with reasons
person (JWT)  approve_order(order_id, option_index) / reject_order   for held orders
```

- The intent is the buyer's signed scope: every search result and every order line is re-checked against its hard constraints (claims that must be present, allergens that must be absent, categories, sizes). Results that do not match are removed, orders that do not match are blocked.
- The agent sends product ids and quantities only. The gateway re-prices every line from Valio Aimo's systems; totals sent by an agent are ignored.
- `idempotency_key` makes `place_order` safe to retry.

## Signed quotes

`get_price_availability` returns:

```json
{
  "quote_id": "7c0e…", "delivery_date": "2026-11-20", "quoted_at": "2026-11-15T10:02:11Z",
  "valid_until": "2026-11-15T10:17:11Z", "key_id": "valio-aimo-7b72cf682a69",
  "signature": "8stuQ7gBTemrPzpX…",
  "offers": [{"product_id": 400104, "unit_price_eur": "1.20", "available_on_date": 900, "sales_block": null, "…": "…"}]
}
```

To trust prices and stock without asking anyone:

1. Fetch `/.well-known/jwks.json` and pick the key whose `kid` equals `key_id` (`kty: OKP`, `crv: Ed25519`).
2. Rebuild the signed bytes: JSON with sorted keys and no whitespace of

   ```json
   {"delivery_date": "...", "offers": [{"available_on_date": 900, "product_id": 400104, "sales_block": null, "unit_price_eur": "1.20"}], "quote_id": "...", "valid_until": "<UTC ISO 8601>"}
   ```

   with offers sorted by `product_id` (reference implementation: `tta_contracts.quote_signing_bytes`).
3. Verify the base64url Ed25519 `signature` over those bytes, and check `valid_until`.

An order built on an expired quote is held for a person (`PRICE_MOVED`), never placed silently. Prices are fixed list prices, not negotiated.

## Decisions and reason codes

The gateway answers `place_order` with one of three outcomes. The mandate (signed by a person, versioned) sets the limits.

| Outcome | When | Reason codes |
| --- | --- | --- |
| `placed` | Everything within the mandate | none |
| `held` | A person must sign off | `OVER_AUTO_LIMIT`, `SUBSTITUTION`, `PARTIAL_AVAILABILITY`, `PRICE_MOVED` |
| `blocked` | Not allowed at all | `MUST_VIOLATED`, `OVER_ORDER_LIMIT`, `OVER_WEEKLY_BUDGET`, `LOT_SIZE` |

Calls can also be refused before any tool runs: `FORBIDDEN` (wrong identity), `AGENT_PAUSED`, `INTENT_INVALID` (unknown, other organisation or expired), `CAP_EXCEEDED`, `RATE_LIMIT`.

A substitute line must name what it replaces (`substitute_for`) and be one of Valio Aimo's own substitute pairs; it is always held for a person.

## Limits

| Limit | Value |
| --- | --- |
| Products per search result | 50 |
| Searches per `search_products_batch` | 30 |
| Product ids per quote / per substitutes call | 50 / 10 |
| Distinct products per intent | 200 (catalog scraping guard) |
| Options per order / lines per option | 4 / 40 |
| Agent calls per minute (gateway) | 60, plus nginx 5 requests per second per key |
| Intent lifetime / quote validity | 24 hours / 15 minutes |

## Audit

Every call, allowed or refused, writes one row to `wholesaler.gateway_events` (tool, identity, decision, reasons, latency, trace id). Valio Aimo reads it in the Grafana dashboard; the buyer organisation reads its own rows with `list_gateway_events`. Traces use W3C trace context (`traceparent` in the MCP request `_meta`), so a buyer's trace and the gateway's spans join into one.

## What is a prototype shortcut

- Both sides share one Supabase project so the gateway can verify the buyer's JWT. In production Valio Aimo would issue its own credentials to mandate signers, or verify a signed mandate (for example AP2-style).
- Agent keys are created by hand (`supabase/seed.sql`); production needs self-service registration per buyer organisation.
- The signing key is generated at start-up unless `GATEWAY_SIGNING_KEY` is set; production would rotate keys and keep old ones in the JWKS until their quotes expire.
- Orders are stored by the gateway: the challenge API has no ordering endpoint.
