# Setting up a new Supabase project

Only for building the whole system on a **new** Supabase project. To run against the team's existing project, follow [`README.md`](../README.md) instead: it needs only the `.env` file.

You need: Docker Desktop, Python 3.12+, a Supabase project (free tier works), an OpenRouter API key, the Valio Aimo API key (challenge PDF, page 5) and, optionally, a Langfuse project. Clone the repository first.

## 1. The challenge data

Put the three challenge files in `data/` (never commit them):

```
data/valio_aimo_junction26_product_data*.json
data/valio_aimo_junction26_delivery_reliability.csv
data/valio_aimo_junction26_carbon_footprint.csv
```

## 2. Database (Supabase)

In the Supabase dashboard, **SQL Editor**, run these files in order:

1. `supabase/migrations/0001_init.sql`
2. `supabase/migrations/0002_audit_dashboard.sql`
3. `supabase/migrations/0003_import_fixes.sql`
4. `supabase/migrations/0005_demo_mandate.sql` (raises the demo limits on a project seeded before 15 Nov; harmless otherwise)

Then **Authentication → Users → Add user**: create the demo buyer (e.g. `manager@demo-hotel.example`, tick *Auto confirm*) and copy its UUID.

## 3. Agent key

The key must start with `tta_`. Generate it and its SHA-256:

```powershell
# PowerShell
$k = "tta_" + -join ((48..57)+(97..122) | Get-Random -Count 32 | % {[char]$_})
$h = -join ([Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($k)) | % { $_.ToString("x2") })
"AGENT_KEY=$k"; "hash: $h"
```

```bash
# bash
k="tta_$(openssl rand -hex 16)"; echo "AGENT_KEY=$k"; printf %s "$k" | sha256sum
```

## 4. Seed the demo organisation

Either with `psql`:

```bash
psql "$DATABASE_URL" -v user_id=<user-uuid> -v agent_key=<AGENT_KEY> -f supabase/seed.sql
```

or in the SQL Editor: open `supabase/seed.sql`, replace `:'user_id'::uuid` with `'<user-uuid>'::uuid` and `:'agent_key'` with `'<AGENT_KEY>'`, run it. To change the key later:

```sql
update wholesaler.agents set key_hash = '<hash>' where id = '00000000-0000-0000-0000-0000000000a1';
```

Give Grafana's read-only role a password (letters and digits):

```sql
alter role dashboard_ro with login password '<GRAFANA_DB_PASSWORD>';
```

## 5. `.env`

```bash
cp .env.example .env
```

| Variable | Where to find it |
| --- | --- |
| `NEXT_PUBLIC_SUPABASE_URL`, `SUPABASE_URL` | Project Settings → API → Project URL |
| `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` | Project Settings → API Keys → publishable key (`sb_publishable_...`) |
| `SUPABASE_PROJECT_REF` | the 20 characters in the project URL |
| `DATABASE_URL` | Connect → **Session pooler** (port 5432). Password: letters and digits only |
| `GRAFANA_DB_HOST` | host part of `DATABASE_URL` |
| `GRAFANA_DB_PASSWORD`, `GRAFANA_ADMIN_PASSWORD` | the password from step 4, and one you choose |
| `OPENROUTER_API_KEY` | openrouter.ai → Keys |
| `VALIO_API_KEY` | challenge PDF; `VALIO_CUSTOMER=123` |
| `AGENT_KEY` | step 3 |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | optional, Langfuse → Settings → API keys. Nothing else is needed for tracing |
| `NEXT_PUBLIC_LANGFUSE_PROJECT_URL` | optional, `https://cloud.langfuse.com/project/<id>`: makes audit rows link to traces |

Check everything (prints no secrets):

```bash
python -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\Activate.ps1
pip install httpx "psycopg[binary]" python-dotenv fastmcp
python scripts/check_env.py
```

## 6. Import the catalog

About 17,500 products, with category labels (LLM) and `baai/bge-m3` embeddings, cached in `data/.cache/`:

```bash
python scripts/import/run.py              # or: docker compose run --rm import
python scripts/import/run.py --limit 200  # quick test first, if you like
```

Then run `supabase/migrations/0004_vector_index.sql` in the SQL Editor (HNSW index, 3 to 8 minutes on the free tier). Without it every search scans all embeddings and can hit the 2-second search timeout.

## 7. Start

```bash
docker compose up -d --build
```

Open http://localhost:8080 and sign in as the demo user. Grafana is on http://localhost:3001 (user `admin`).

After changing a `NEXT_PUBLIC_*` value: `docker compose up -d --build web`. After changing anything else in `.env`: `docker compose up -d api mcp`.

## Tests

```bash
cd services/mcp && uv run pytest        # gateway; MCP_TEST_DATABASE_URL=<empty local Postgres> enables DB tests
cd services/api && uv run pytest        # buyer agent; TEST_DATABASE_URL=<local Postgres, db name tta_api_test_*>
cd apps/web && npm ci && npx tsc --noEmit && npm run build
```
