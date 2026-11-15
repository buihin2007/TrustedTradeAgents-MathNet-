# Trusted Trade Agents

Junction 2026 · Valio Aimo track. A kitchen buyer tells a purchasing agent what they need ("lunch for 80 next Tuesday, nut-free"). The agent asks only what is missing, suggests dishes from what Valio Aimo has in stock, and orders through **Valio Aimo's own MCP gateway**, which re-prices everything and decides whether the order is placed, held for approval or blocked.

This file is only about installing and running it. How it works, and how it maps to the challenge: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

| What | Where |
| --- | --- |
| Buyer app | http://localhost:8080 |
| Valio Aimo audit dashboard (Grafana) | http://localhost:3001 |
| MCP gateway, for any external agent | http://localhost:8080/mcp ([`docs/AGENT_INTEGRATION.md`](docs/AGENT_INTEGRATION.md)) |
| Quote signing key (JWKS) | http://localhost:8080/.well-known/jwks.json |

## Run it

The database, the catalog and the demo accounts already live in the team's Supabase project. Your machine only runs the services.

You need: [Docker Desktop](https://www.docker.com/products/docker-desktop/) and Git. Python 3.12+ only for the optional checks and demo scripts.

1. Get the code:

   ```bash
   git clone https://github.com/buihin2007/TrustedTradeAgents-MathNet-.git
   cd TrustedTradeAgents-MathNet-
   ```

2. Ask the team for the `.env` file and put it in the repository root. It holds passwords and API keys: it is never committed and never sent through a public channel.

3. Start everything:

   ```bash
   docker compose up -d --build
   ```

   The first build takes a few minutes. `docker compose ps` lists the running services; the app answers on port 8080 once `web` is up.

4. Open http://localhost:8080 and sign in with the demo buyer account the team gives you.

Optional check, prints no secrets:

```bash
python -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\Activate.ps1
pip install httpx "psycopg[binary]" python-dotenv fastmcp
python scripts/check_env.py
```

Stop: `docker compose down`. After pulling new code: `docker compose up -d --build`.

Everyone who runs it shares one database: requests, held orders and the weekly budget are shared too. During a demo, let one person send requests.

## Try it

| Message | What happens |
| --- | --- |
| `I need 1,000 litres of lactose-free milk by Monday` | The challenge's example: above the 300 EUR auto limit, so the gateway holds it; approve it on the Approvals page |
| `20 kg chicken fillet for Friday` | Complete and under the auto limit: the agent orders without asking |
| `Lunch for 80 people next Tuesday, nut-free` | Three dishes checked against stock for that day; nut allergens excluded by the gateway |
| `What should I cook for 100 guests on Saturday?` | Asks only what it cannot know, suggests dishes, offers others if ingredients are missing |
| `hello how are you` | Rejected at once: not a purchasing request |

The Requests page shows what the agent may do per order (buy alone, wait for you, blocked). Change the limits on the Mandate page.

Demo scripts (Python, from the repository root):

```bash
python scripts/rogue_agent.py      # a misbehaving agent: scraping, unrequested items, user-only tools, bursts. Expect 5/5 STOPPED
python scripts/diagnose.py         # why the last request was blocked or slow (gateway events with latency)
python scripts/demo/record_demo.py --rogue --grafana   # records the whole demo as a video (pip install playwright)
```

Traces: if the team's `.env` has Langfuse keys, open Langfuse → **Sessions**. One session is one request; see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#audit-and-traceability).

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| Port 8080 or 3001 already in use | Stop the other program, or change the port in `docker-compose.yml` |
| Login fails | Wrong account, or `.env` is not in the repository root: `docker compose up -d --build web` after fixing it |
| "The credentials were not accepted" | The gateway could not verify your login with Supabase (slow network) or the agent key is wrong. `docker compose logs mcp --since 10m` says which; `python scripts/diagnose.py` checks the key |
| "Gateway is temporarily unavailable" on catalog search | Slow vector search. Add `SEARCH_BATCH_VECTORS=0` to `.env`, then `docker compose up -d mcp` |
| A request is "Blocked" with `OVER_WEEKLY_BUDGET` | The shared weekly budget is used up. Raise it on the Mandate page |
| Everything "not in stock" | That delivery day is past the order cut-off or has no stock. `python scripts/valio_probe.py <date> <product id>` shows Valio's raw answer |
| `max clients reached in session mode` | Too many people run the stack against one Supabase project: stop the ones not in use |
| No traces in Langfuse | `LANGFUSE_*` missing in `.env`; then `docker compose up -d api mcp` |

## More

| Document | For |
| --- | --- |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | System architecture, request logic, gateway rules, and the challenge requirements mapped to features |
| [`docs/AGENT_INTEGRATION.md`](docs/AGENT_INTEGRATION.md) | How any external MCP agent buys through the gateway |
| [`docs/SETUP_FROM_SCRATCH.md`](docs/SETUP_FROM_SCRATCH.md) | Building a new Supabase project from nothing (migrations, seed, catalog import) |
| [`docs/PLAN.md`](docs/PLAN.md) | The full development plan (Vietnamese) |
| [`CLAUDE.md`](CLAUDE.md) | Rules for anyone, human or AI, changing the code |
