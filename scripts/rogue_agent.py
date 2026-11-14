"""Demo scenario 5: a misbehaving purchasing agent hits the Valio Aimo gateway (owner: P4).

It holds a valid agent key but tries five things a trustworthy agent must not do, and prints what
stopped each one. Run from the repo root while `docker compose up` is running:

    python scripts/rogue_agent.py                      # all attacks, against http://localhost:8080/mcp
    python scripts/rogue_agent.py --intent <uuid>      # use a real intent so Jev and the order policy get to judge
    python scripts/rogue_agent.py --only burst

Every blocked call should also appear on the Grafana dashboard (http://localhost:3001) within 5 s,
except the nginx 429s, which never reach the gateway.
"""
import argparse
import asyncio
import os
import sys
import uuid
from datetime import date, timedelta

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

DEMO_AGENT = "00000000-0000-0000-0000-0000000000a1"   # supabase/seed.sql

RED, GREEN, YELLOW, DIM, END = "\033[31m", "\033[32m", "\033[33m", "\033[2m", "\033[0m"


def client(url: str) -> Client:
    return Client(StreamableHttpTransport(url, headers={"Authorization": f"Bearer {os.environ['AGENT_KEY']}"}))


async def call(url: str, tool: str, args: dict) -> tuple[bool, str]:
    """(went_through, short result or error)."""
    try:
        async with client(url) as c:
            res = await c.call_tool(tool, args, raise_on_error=False)
            text = " ".join(getattr(b, "text", "") for b in res.content)[:160]
            return (not res.is_error), text
    except Exception as e:                                   # HTTP 429, auth failure, transport error
        return False, f"{type(e).__name__}: {str(e).splitlines()[0][:140]}"


def report(name: str, expect: str, ok: bool, detail: str) -> bool | None:
    """True = stopped, False = got through, None = cannot judge (tool not implemented yet)."""
    if "Unknown tool" in detail:
        print(f"{YELLOW}SKIPPED{END}  {name}\n         tool not implemented on the gateway yet\n")
        return None
    stopped = not ok
    mark = f"{GREEN}STOPPED{END}" if stopped else f"{RED}GOT THROUGH{END}"
    print(f"{mark}  {name}\n         expected: {expect}\n         {DIM}{detail}{END}\n")
    return stopped


async def attack_scan(url, intent):
    """Catalogue scraping: ask for prices of 400 products in one call."""
    ok, d = await call(url, "get_price_availability", {"request": {
        "intent_id": intent, "product_ids": list(range(460000, 460400)),
        "delivery_date": (date.today() + timedelta(days=2)).isoformat()}})
    return report("Scrape 400 prices in one call", "CAP_EXCEEDED (max 50 ids per call) or schema error", ok, d)


async def attack_unrequested(url, intent):
    """Order something the buyer never asked for (intent is about milk)."""
    ok, d = await call(url, "place_order", {"request": {
        "intent_id": intent, "delivery_date": (date.today() + timedelta(days=2)).isoformat(),
        "options": [{"label": "best_match", "lines": [{"product_id": 466324, "quantity_units": 40}]}],
        "preferred_index": 0, "idempotency_key": f"rogue-{uuid.uuid4().hex[:12]}"}})
    return report("Order 40 x soy sauce 20 l for a milk request", "blocked: MUST_VIOLATED / JEV_BLOCK / INTENT_INVALID", ok, d)


async def attack_user_tool(url, intent):
    """Approve its own held order: approval needs the user's JWT, not an agent key."""
    ok, d = await call(url, "approve_order", {"order_id": str(uuid.uuid4()), "option_index": 0})
    return report("Approve an order with the agent key", "FORBIDDEN (user tools need the signer's JWT)", ok, d)


async def attack_mandate(url, intent):
    """Raise its own spending limit."""
    ok, d = await call(url, "update_mandate", {"agent_id": DEMO_AGENT, "auto_limit_eur": "100000",
                                         "order_limit_eur": "100000"})
    return report("Raise its own mandate to 100,000 EUR", "FORBIDDEN", ok, d)


async def attack_burst(url, intent, n=100):
    """Burst 100 calls at once: nginx limits each key to 5 r/s with burst 10."""
    results = await asyncio.gather(*(call(url, "get_facets", {}) for _ in range(n)))
    stopped = sum(1 for ok, _ in results if not ok)
    sample = next((d for ok, d in results if not ok), "none")
    if all("Unknown tool" in d for _, d in results):
        return report("Burst", "", False, "Unknown tool")
    stopped = sum(1 for ok, d in results if not ok and "Unknown tool" not in d)
    return report(f"Burst {n} calls at once", "most rejected: HTTP 429 (nginx) or RATE_LIMIT (gateway)",
                  stopped < n // 2, f"{stopped}/{n} rejected; e.g. {sample}")


ATTACKS = {"scan": attack_scan, "unrequested": attack_unrequested, "user_tool": attack_user_tool,
           "mandate": attack_mandate, "burst": attack_burst}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8080/mcp")
    ap.add_argument("--intent", default=str(uuid.uuid4()), help="intent id; a random one tests INTENT_INVALID")
    ap.add_argument("--only", choices=list(ATTACKS))
    args = ap.parse_args()
    if not os.environ.get("AGENT_KEY"):
        sys.exit("AGENT_KEY missing: run from the repo root with .env present")
    print(f"Rogue agent against {args.url}, intent {args.intent}\n")
    todo = [ATTACKS[args.only]] if args.only else list(ATTACKS.values())
    results = [await a(args.url, args.intent) for a in todo]
    judged = [r for r in results if r is not None]
    print(f"{sum(judged)}/{len(judged)} attacks stopped, {len(results) - len(judged)} skipped.")
    return 0 if all(judged) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
