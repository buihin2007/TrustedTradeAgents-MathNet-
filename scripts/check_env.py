# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx>=0.27", "psycopg[binary]>=3.2", "python-dotenv>=1.0"]
# ///
"""Check that .env is complete and every external service answers. Never prints secret values.

Run from the repo root:  uv run scripts/check_env.py
(or: pip install httpx "psycopg[binary]" python-dotenv && python3 scripts/check_env.py)
"""
import base64
import hashlib
import os
import re
import sys
from datetime import date, timedelta

import httpx
import psycopg
from dotenv import dotenv_values

ENV = {k: (v or "").strip() for k, v in dotenv_values(".env").items()}
OK, BAD, WARN = "\033[32mOK  \033[0m", "\033[31mFAIL\033[0m", "\033[33mWARN\033[0m"
failures = 0


def report(status: str, name: str, detail: str = "") -> None:
    global failures
    failures += status == BAD
    print(f"{status} {name}{(' - ' + detail) if detail else ''}")


def get(k: str) -> str:
    return ENV.get(k, "")


# ------------------------------------------------------------------ 1. presence and format
print("\n== 1. Biến môi trường ==")
if not ENV:
    sys.exit(f"{BAD} không tìm thấy .env ở thư mục hiện tại ({os.getcwd()}). Chạy từ gốc repo.")

rules = {
    "NEXT_PUBLIC_SUPABASE_URL": r"^https://[a-z0-9]{20}\.supabase\.co/?$",
    "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY": r"^sb_publishable_",
    "SUPABASE_URL": r"^https://[a-z0-9]{20}\.supabase\.co/?$",
    "DATABASE_URL": r"^postgres(ql)?://postgres\.[a-z0-9]{20}:.+@.+pooler\.supabase\.com:5432/postgres",
    "OPENROUTER_API_KEY": r"^sk-or-",
    "LLM_MODEL": r"^~?[\w.-]+/[\w.:-]+$",
    "EMBED_MODEL": r"^[\w.-]+/[\w.:-]+$",
    "JEV_MODEL": r"typesafe/jev",
    "VALIO_API_BASE": r"^https://",
    "VALIO_API_KEY": r"^junct_",
    "VALIO_CUSTOMER": r".+",
    "AGENT_KEY": r"^.{16,}$",
    "MCP_URL": r"^http://nginx/mcp$",
    "LANGFUSE_PUBLIC_KEY": r"^pk-lf-(?!pk-lf-)",
    "LANGFUSE_SECRET_KEY": r"^sk-lf-(?!sk-lf-)",
    "LANGFUSE_BASE_URL": r"^https://",
    "SUPABASE_PROJECT_REF": r"^[a-z0-9]{20}$",
    "GRAFANA_DB_HOST": r"pooler\.supabase\.com$",
    "GRAFANA_DB_PASSWORD": r"^[A-Za-z0-9]{8,}$",
    "GRAFANA_ADMIN_PASSWORD": r".+",
}
for k, pattern in rules.items():
    v = get(k)
    if not v:
        report(BAD, k, "thiếu hoặc rỗng")
    elif v.startswith(("'", '"')):
        report(WARN, k, "có dấu ngoặc kép; docker compose env_file giữ nguyên dấu ngoặc, nên bỏ đi")
    elif not re.search(pattern, v):
        report(BAD, k, f"sai định dạng (mong đợi khớp {pattern})")
    else:
        report(OK, k)

ref = get("SUPABASE_PROJECT_REF")
if ref and ref not in get("SUPABASE_URL"):
    report(BAD, "SUPABASE_PROJECT_REF", "không khớp với SUPABASE_URL")
if ref and f"postgres.{ref}:" not in get("DATABASE_URL"):
    report(BAD, "DATABASE_URL", "username không phải postgres.<SUPABASE_PROJECT_REF>")
if get("GRAFANA_DB_HOST") and get("GRAFANA_DB_HOST") not in get("DATABASE_URL"):
    report(WARN, "GRAFANA_DB_HOST", "khác host trong DATABASE_URL")
if get("EMBED_MODEL") != "baai/bge-m3":
    report(WARN, "EMBED_MODEL", "plan chốt baai/bge-m3 (1024 chiều)")

client = httpx.Client(timeout=20)

# ------------------------------------------------------------------ 2. Supabase
print("\n== 2. Supabase ==")
try:
    r = client.get(f"{get('SUPABASE_URL').rstrip('/')}/auth/v1/.well-known/jwks.json")
    report(OK if r.status_code == 200 else BAD, "Auth JWKS", f"HTTP {r.status_code}")
except Exception as e:
    report(BAD, "Auth JWKS", type(e).__name__)

db_dims = None
try:
    with psycopg.connect(get("DATABASE_URL"), connect_timeout=10) as conn:
        report(OK, "DATABASE_URL kết nối được")
        cur = conn.cursor()
        tables = {r[0] for r in cur.execute(
            "select schemaname||'.'||tablename from pg_tables where schemaname in ('buyer','wholesaler')")}
        report(OK if len(tables) >= 14 else BAD, "Migration 0001", f"{len(tables)} bảng trong buyer/wholesaler")
        views = {r[0] for r in cur.execute("select viewname from pg_views where schemaname='wholesaler'")}
        report(OK if "v_audit_gateway" in views else BAD, "Migration 0002", "có v_audit_*" if "v_audit_gateway" in views else "chưa chạy 0002")
        db_dims = cur.execute("""select atttypmod from pg_attribute
            where attrelid = 'wholesaler.products'::regclass and attname = 'embedding'""").fetchone()[0]
        report(OK, "Cột embedding", f"vector({db_dims})")
        n = cur.execute("select count(*) from wholesaler.products").fetchone()[0]
        report(OK if n else WARN, "Sản phẩm", f"{n} dòng" + ("" if n else " (chưa import)"))
        row = cur.execute("select key_hash from wholesaler.agents where id = '00000000-0000-0000-0000-0000000000a1'").fetchone()
        if not row:
            report(BAD, "Seed", "chưa có agent demo")
        else:
            match = row[0] == hashlib.sha256(get("AGENT_KEY").encode()).hexdigest()
            report(OK if match else BAD, "AGENT_KEY", "khớp hash trong DB" if match else "KHÔNG khớp hash trong DB, chạy lại lệnh update key_hash")
        counts = cur.execute("select (select count(*) from wholesaler.mandates), (select count(*) from buyer.profiles)").fetchone()
        report(OK if all(counts) else BAD, "Mandate và profile", f"{counts[0]} mandate, {counts[1]} profile")
        rls = cur.execute("select count(*) from pg_tables where schemaname in ('buyer','wholesaler') and not rowsecurity").fetchone()[0]
        report(OK if rls == 0 else WARN, "RLS dự phòng", "bật trên mọi bảng" if rls == 0 else f"{rls} bảng chưa bật")
except Exception as e:
    report(BAD, "DATABASE_URL", f"{type(e).__name__}: {str(e).splitlines()[0][:120]}")

try:
    host, pw = get("GRAFANA_DB_HOST"), get("GRAFANA_DB_PASSWORD")
    with psycopg.connect(host=host, port=5432, dbname="postgres", user=f"dashboard_ro.{ref}",
                         password=pw, sslmode="require", connect_timeout=10) as conn:
        n = conn.execute("select count(*) from wholesaler.v_audit_gateway").fetchone()[0]
        report(OK, "dashboard_ro (Grafana)", f"đọc được v_audit_gateway, {n} dòng")
except Exception as e:
    report(BAD, "dashboard_ro (Grafana)", f"{type(e).__name__}: {str(e).splitlines()[0][:120]}")

# ------------------------------------------------------------------ 3. OpenRouter
print("\n== 3. OpenRouter ==")
OR = {"Authorization": f"Bearer {get('OPENROUTER_API_KEY')}"}
try:
    r = client.get("https://openrouter.ai/api/v1/key", headers=OR)
    if r.status_code == 200:
        d = r.json().get("data", {})
        report(OK, "API key", f"đã dùng ${d.get('usage', 0):.4f}, hạn mức {d.get('limit')}")
    else:
        report(BAD, "API key", f"HTTP {r.status_code}")
except Exception as e:
    report(BAD, "API key", type(e).__name__)

try:
    r = client.post("https://openrouter.ai/api/v1/embeddings", headers=OR,
                    json={"model": get("EMBED_MODEL"), "input": ["laktoositon maito 1 l"]})
    dims = len(r.json()["data"][0]["embedding"]) if r.status_code == 200 else None
    if dims is None:
        report(BAD, "Embeddings", f"HTTP {r.status_code}: {r.text[:120]}")
    elif db_dims and dims != db_dims:
        report(BAD, "Embeddings", f"model trả {dims} chiều nhưng cột DB là vector({db_dims}); chạy ALTER đổi cột")
    else:
        report(OK, "Embeddings", f"{get('EMBED_MODEL')} trả {dims} chiều")
except Exception as e:
    report(BAD, "Embeddings", type(e).__name__)

schema = {"type": "object", "properties": {"query_text_alt": {"type": "string"}},
          "required": ["query_text_alt"], "additionalProperties": False}
for var in ("LLM_MODEL", "LLM_MODEL_SQL"):
    model = get(var)
    if not model:
        continue
    models = [model] + ([get("LLM_MODEL_FALLBACK")] if get("LLM_MODEL_FALLBACK") else [])
    try:
        r = client.post("https://openrouter.ai/api/v1/chat/completions", headers=OR, timeout=60, json={
            "models": models,
            "messages": [{"role": "user", "content": "Translate to Finnish as JSON: lactose free milk 1 l"}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "t", "strict": True, "schema": schema}},
            "provider": {"sort": "price", "require_parameters": True},
            "max_tokens": 200})
        if r.status_code == 200:
            served = r.json().get("model", "?")
            fell_back = served.split(":")[0] != model.split(":")[0]
            report(WARN if fell_back else OK, var, f"structured output chạy, model thực tế: {served}"
                   + (" (đã rơi xuống fallback)" if fell_back else ""))
        else:
            report(BAD, var, f"HTTP {r.status_code}: {r.text[:150]}")
    except Exception as e:
        report(BAD, var, type(e).__name__)

try:
    r = client.post("https://openrouter.ai/api/alpha/decisions", headers=OR, json={
        "model": get("JEV_MODEL"),
        "state": {"request": "40 l lactose free milk", "tool_call": {"tool": "search_products", "query": "lactose free milk"}},
        "questions": {"serves_intent": {"type": "noul", "instructions": "The tool call is a reasonable step towards the request."}}})
    if r.status_code == 200:
        p = r.json().get("answers", {}).get("serves_intent")
        report(OK, "Jev", f"serves_intent = {p}")
    else:
        report(BAD, "Jev", f"HTTP {r.status_code}: {r.text[:150]}")
except Exception as e:
    report(BAD, "Jev", type(e).__name__)

# ------------------------------------------------------------------ 4. Valio Aimo mock API
print("\n== 4. Valio Aimo API ==")
VB, VH = get("VALIO_API_BASE").rstrip("/"), {"X-API-Key": get("VALIO_API_KEY")}
try:
    r = client.get(f"{VB}/api/ping", headers=VH)
    report(OK if r.status_code == 200 else BAD, "ping", f"HTTP {r.status_code}")
    body = {"customer": get("VALIO_CUSTOMER"), "requestedDate": (date.today() + timedelta(days=2)).isoformat(),
            "products": ["464201"]}
    for ep in ("price", "availability"):
        r = client.post(f"{VB}/api/{ep}", headers=VH, json=body)
        report(OK if r.status_code == 200 else BAD, ep, f"HTTP {r.status_code}" + ("" if r.status_code == 200 else f": {r.text[:120]}"))
except Exception as e:
    report(BAD, "Valio API", type(e).__name__)

# ------------------------------------------------------------------ 5. Langfuse OTLP
print("\n== 5. Langfuse ==")
try:
    auth = base64.b64encode(f"{get('LANGFUSE_PUBLIC_KEY')}:{get('LANGFUSE_SECRET_KEY')}".encode()).decode()
    r = client.post(f"{get('LANGFUSE_BASE_URL').rstrip('/')}/api/public/otel/v1/traces",
                    headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json",
                             "x-langfuse-ingestion-version": "4"},
                    content=b'{"resourceSpans":[]}')
    report(OK if r.status_code in (200, 202) else BAD, "OTLP endpoint", f"HTTP {r.status_code}"
           + (" (sai key)" if r.status_code == 401 else ""))
except Exception as e:
    report(BAD, "OTLP endpoint", type(e).__name__)

print(f"\n{'Tất cả đều ổn.' if failures == 0 else f'{failures} mục FAIL, sửa rồi chạy lại.'}")
sys.exit(1 if failures else 0)
