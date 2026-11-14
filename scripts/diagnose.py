# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx>=0.27", "psycopg[binary]>=3.2", "python-dotenv>=1.0", "fastmcp>=4.2,<5"]
# ///
"""Find out why a buyer request was blocked (e.g. FORBIDDEN). Never prints secret values.

Run from the repo root with the stack up (docker compose up -d):
    python scripts/diagnose.py                      # uses manager@demo-hotel.example
    python scripts/diagnose.py --email other@x.com
Needs: pip install httpx "psycopg[binary]" python-dotenv fastmcp
"""
import argparse
import asyncio
import hashlib
import sys

import httpx
import psycopg
from dotenv import dotenv_values
from psycopg.rows import dict_row

ENV = {k: (v or "").strip() for k, v in dotenv_values(".env").items()}
OK, BAD, WARN, INFO = "\033[32mOK  \033[0m", "\033[31mFAIL\033[0m", "\033[33mWARN\033[0m", "    "
USER_TOOLS = {"register_intent", "approve_order", "reject_order", "get_mandate", "update_mandate",
              "set_agent_status", "list_gateway_events"}
hints: list[str] = []


def say(status: str, name: str, detail: str = "") -> None:
    print(f"{status} {name}{(' - ' + detail) if detail else ''}")


def check_key() -> None:
    print("\n== 1. AGENT_KEY ==")
    key = ENV.get("AGENT_KEY", "")
    if not key.startswith("tta_"):
        say(BAD, "AGENT_KEY", f"không bắt đầu bằng 'tta_' (dài {len(key)}). Gateway coi nó là JWT người dùng -> FORBIDDEN")
        hints.append("Tạo key mới có tiền tố tta_, ghi vào .env, cập nhật key_hash trong DB, rồi "
                     "docker compose up -d --force-recreate api mcp (xem hướng dẫn trong chat).")
    else:
        say(OK, "AGENT_KEY", f"có tiền tố tta_, dài {len(key)}")


def check_db(email: str) -> None:
    print("\n== 2. Dữ liệu seed và request gần nhất ==")
    key_hash = hashlib.sha256(ENV.get("AGENT_KEY", "").encode()).hexdigest()
    with psycopg.connect(ENV["DATABASE_URL"], row_factory=dict_row, connect_timeout=8) as db:
        one = lambda sql, *a: db.execute(sql, a).fetchone()      # noqa: E731
        rows = lambda sql, *a: db.execute(sql, a).fetchall()     # noqa: E731

        agent = one("select id::text, org_id::text, status from wholesaler.agents where key_hash = %s", key_hash)
        if agent:
            say(OK if agent["status"] == "active" else BAD, "agent", f"{agent['id']} status={agent['status']}")
            if agent["status"] != "active":
                hints.append("Agent đang paused: bấm Resume ở trang Mandate.")
        else:
            say(BAD, "agent", "không có agent nào có key_hash = sha256(AGENT_KEY)")
            hints.append("Hash của AGENT_KEY không có trong wholesaler.agents: chạy lại lệnh update key_hash.")

        user = one("select id::text from auth.users where email = %s", email)
        if not user:
            say(BAD, "user", f"không có {email} trong auth.users")
            return
        uid = user["id"]
        members = rows("select org_id::text, role from wholesaler.org_members where user_id = %s", uid)
        good = len(members) == 1 and members[0]["role"] == "signer"
        say(OK if good else BAD, "org_members", f"{len(members)} dòng {[m['role'] for m in members]}")
        if not good:
            hints.append("User phải có đúng 1 dòng org_members với role 'signer' (gateway đòi đúng 1 org).")
        profile = one("select org_id::text, agent_id::text from buyer.profiles where user_id = %s", uid)
        say(OK if profile else BAD, "buyer.profiles", str(profile) if profile else "thiếu")
        if profile and agent and (profile["agent_id"] != agent["id"] or profile["org_id"] != agent["org_id"]):
            say(BAD, "profile vs agent", "profile trỏ tới agent/org khác agent của AGENT_KEY")
            hints.append("buyer.profiles.agent_id phải là agent có hash của AGENT_KEY.")
        mandate = one("select max(version) as v from wholesaler.mandates where agent_id = %s",
                      profile["agent_id"] if profile else None)
        say(OK if mandate and mandate["v"] else BAD, "mandate", f"version {mandate['v'] if mandate else None}")

        req = one("""select id::text, text, status, step, error, created_at from buyer.requests
                     where user_id = %s order by created_at desc limit 1""", uid)
        if not req:
            say(WARN, "request", "chưa có request nào")
            return
        print(f"\n{INFO}Request gần nhất: \"{req['text']}\" status={req['status']} step={req['step']} error={req['error']}")
        print(f"{INFO}Audit của request này:")
        for e in rows("""select event_type, decision, summary, details from buyer.audit_events
                         where request_id = %s order by seq""", req["id"]):
            print(f"{INFO}  {e['event_type']:<22} {e['decision']:<18} {e['summary']}  {e['details'] or ''}")
        events = rows("""select ts, tool, actor_kind, actor_id, decision, reasons from wholesaler.gateway_events
                         where ts >= %s - interval '30 seconds' order by ts limit 30""", req["created_at"])
        print(f"{INFO}gateway_events từ lúc tạo request ({len(events)}):")
        for e in events:
            who = "CHƯA XÁC THỰC" if e["actor_id"] == "unauthenticated" else e["actor_kind"]
            print(f"{INFO}  {e['ts']:%H:%M:%S} {e['tool']:<24} {who:<14} {e['decision']:<9} {','.join(e['reasons'])}")

        denied = [e for e in events if "FORBIDDEN" in e["reasons"]]
        if not events:
            hints.append("Không có gateway_events nào: request không tới được gateway. FORBIDDEN đến từ API "
                         "(kiểm tra docker compose logs api).")
        for e in denied[:1]:
            if e["actor_id"] == "unauthenticated" and e["tool"] in USER_TOOLS:
                hints.append(f"{e['tool']} bị từ chối vì gateway KHÔNG xác minh được JWT của user. "
                             "Xem mục 3 (JWKS) và docker compose logs mcp.")
            elif e["actor_id"] == "unauthenticated":
                hints.append(f"{e['tool']} bị từ chối vì gateway không nhận ra AGENT_KEY (xem mục 1 và 2).")
            else:
                hints.append(f"{e['tool']} bị từ chối dù đã xác thực ({e['actor_kind']}): sai vai trò hoặc "
                             "không phải signer.")


def check_jwks() -> None:
    print("\n== 3. Supabase JWT ==")
    base = (ENV.get("SUPABASE_URL") or ENV.get("NEXT_PUBLIC_SUPABASE_URL", "")).rstrip("/")
    try:
        r = httpx.get(f"{base}/auth/v1/.well-known/jwks.json", timeout=8)
        algs = [k.get("alg") for k in r.json().get("keys", [])] if r.status_code == 200 else []
        if algs:
            say(OK, "JWKS", f"HTTP {r.status_code}, khóa {algs}: gateway xác minh JWT bằng khóa công khai")
        else:
            say(WARN, "JWKS", f"HTTP {r.status_code}, không có khóa bất đối xứng: project dùng HS256 cũ, "
                "gateway sẽ hỏi /auth/v1/user bằng NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY")
    except Exception as e:
        say(BAD, "JWKS", f"{type(e).__name__}: {e}")


async def check_gateway(base: str) -> None:
    print("\n== 4. Gateway thật qua nginx ==")
    try:
        r = httpx.get(f"{base}/api/health", timeout=5)
        say(OK if r.status_code == 200 else BAD, "/api/health", f"HTTP {r.status_code}")
    except Exception as e:
        say(BAD, "/api/health", f"{type(e).__name__}: stack chưa chạy? (docker compose up -d)")
        return
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport

    async def facets(key: str) -> tuple[bool, str]:
        transport = StreamableHttpTransport(f"{base}/mcp", headers={"Authorization": f"Bearer {key}"})
        try:
            async with Client(transport, timeout=20) as c:
                res = await c.call_tool("get_facets", {}, raise_on_error=False)
                text = " ".join(getattr(b, "text", "") for b in res.content)[:120]
                return not res.is_error, text
        except Exception as e:
            return False, f"{type(e).__name__}: {str(e).splitlines()[0][:120]}"

    ok, detail = await facets(ENV.get("AGENT_KEY", ""))
    say(OK if ok else BAD, "get_facets bằng AGENT_KEY", "được phép" if ok else detail)
    ok, detail = await facets("tta_this_key_does_not_exist_000")
    say(OK if not ok else BAD, "get_facets bằng key giả", f"bị chặn: {detail}" if not ok else "LỌT QUA")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", default="manager@demo-hotel.example")
    ap.add_argument("--base", default="http://localhost:8080")
    args = ap.parse_args()
    if not ENV:
        sys.exit("Không thấy .env: chạy từ thư mục gốc repo.")
    check_key()
    try:
        check_db(args.email)
    except Exception as e:
        say(BAD, "database", f"{type(e).__name__}: {str(e).splitlines()[0][:160]}")
    check_jwks()
    asyncio.run(check_gateway(args.base))
    print("\n== Kết luận ==")
    for h in dict.fromkeys(hints):
        print(f"-> {h}")
    if not hints:
        print("Không thấy lỗi cấu hình. Gửi output của: docker compose logs mcp api --tail 80")
    return 1 if hints else 0


if __name__ == "__main__":
    sys.exit(main())
