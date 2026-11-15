"""Print Valio Aimo's raw price and availability answer for a few products (no secrets are printed).

    docker compose exec mcp python /app/../scripts/valio_probe.py            # if scripts is mounted
    python scripts/valio_probe.py 2026-11-20 404165 400104                  # from the repo root with .env
"""
import json
import sys
from datetime import date, timedelta

import httpx
from dotenv import dotenv_values

env = dotenv_values(".env")
day = sys.argv[1] if len(sys.argv) > 1 else (date.today() + timedelta(days=3)).isoformat()
ids = sys.argv[2:] or ["400104", "404165"]
body = {"customer": env["VALIO_CUSTOMER"], "requestedDate": day, "products": ids}
for endpoint in ("availability", "price"):
    r = httpx.post(f"{env['VALIO_API_BASE'].rstrip('/')}/api/{endpoint}", json=body,
                   headers={"X-API-Key": env["VALIO_API_KEY"]}, timeout=15)
    print(f"== {endpoint} HTTP {r.status_code}")
    print(json.dumps(r.json(), indent=1, ensure_ascii=False)[:2500])
