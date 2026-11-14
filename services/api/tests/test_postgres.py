"""Real SQL/concurrency checks, only against an explicitly supplied local test DB.

Creates a uniquely named database, applies the buyer section of the actual P1
migration, and drops only that test-created database at the end. Never use the
team Supabase URL here. Unit tests skip this module when no local DB is supplied.
"""

import asyncio
import os
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
import pytest_asyncio
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from tta_contracts import AuditEvent, verify_chain

from app.models import Profile
from app.options import build_options
from app.store import Conflict, PostgresStore
from tests.factories import AGENT_ID, ORG_ID, PROFILE, hit, offer, order, parsed

pytestmark = pytest.mark.database


@pytest.fixture(scope="session")
def postgres_url():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to an explicitly disposable local PostgreSQL database")
    options = conninfo_to_dict(url)
    if options.get("host") not in {"localhost", "127.0.0.1", "::1"}:
        pytest.fail("Database tests only accept localhost; never point them at Supabase")
    if not options.get("dbname", "").startswith("tta_api_test_"):
        pytest.fail("TEST_DATABASE_URL database must start with tta_api_test_")
    database_name = "tta_api_test_" + uuid4().hex
    temporary_url = make_conninfo(url, dbname=database_name)
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
        try:
            migration = (Path(__file__).resolve().parents[3] / "supabase/migrations/0001_init.sql").read_text(
                encoding="utf-8"
            )
            buyer_sql = migration.split("-- ============================================================ buyer", 1)[1]
            with psycopg.connect(temporary_url) as conn:
                conn.execute("CREATE SCHEMA buyer")
                conn.execute(buyer_sql)
            yield temporary_url
        finally:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database_name)))


@pytest_asyncio.fixture
async def db(postgres_url):
    store = PostgresStore(postgres_url)
    await store.open()
    try:
        yield store
    finally:
        await store.close()


async def test_real_request_options_roundtrip_and_owner_filter(db):
    request = await db.create_request(PROFILE, "milk'); DROP TABLE buyer.requests; --")
    await db.update_request(PROFILE, request.id, parsed=parsed().model_dump(mode="json"))
    await db.save_options(PROFILE, request.id, build_options(parsed(), [hit()], [offer()]))
    result = await db.request(PROFILE, request.id)
    assert result.text == "milk'); DROP TABLE buyer.requests; --"
    assert result.options[0].option.lines[0].quantity_units == 40
    other = Profile(user_id=uuid4(), org_id=ORG_ID, agent_id=AGENT_ID)
    assert await db.request(other, request.id) is None
    assert db.pool.max_size == 2


async def test_real_concurrent_audit_appends_preserve_global_chain_and_trigger(db):
    request = await db.create_request(PROFILE, "milk")
    await asyncio.gather(
        *[db.append_event(PROFILE, request.id, "request.created", f"Concurrent event {index}") for index in range(20)]
    )
    async with db.pool.connection() as conn:
        rows = await (await conn.execute("SELECT * FROM buyer.audit_events ORDER BY seq")).fetchall()
    events = [
        AuditEvent.model_validate({**row, "request_id": str(row["request_id"]) if row["request_id"] else None})
        for row in rows
    ]
    assert verify_chain(events) is None
    events[-1].summary = "tampered"
    assert verify_chain(events) == events[-1].seq
    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        async with db.pool.connection() as conn:
            await conn.execute("UPDATE buyer.audit_events SET summary='tampered' WHERE seq=%s", (events[-1].seq,))


async def test_real_concurrent_answer_only_advances_once(db):
    request = await db.create_request(PROFILE, "milk")
    question = await db.ask(PROFILE, request.id, "How many litres?", ["40", "50"])
    results = await asyncio.gather(
        db.answer(PROFILE, request.id, question.id, "40"),
        db.answer(PROFILE, request.id, question.id, "50"),
        return_exceptions=True,
    )
    assert sum(isinstance(result, Conflict) for result in results) == 1
    result = await db.request(PROFILE, request.id)
    assert result.status == "running"
    assert result.questions[0].answer in {"40", "50"}


async def test_order_update_rolls_back_option_replacement_on_invalid_id(db):
    request = await db.create_request(PROFILE, "milk")
    options = build_options(parsed(), [hit()], [offer()])
    await db.save_options(PROFILE, request.id, options)
    with pytest.raises(ValueError):
        await db.record_order(PROFILE, request.id, order(order_id="invalid-uuid"))
    result = await db.request(PROFILE, request.id)
    assert result.status == "running"
    assert result.order_id is None
    assert result.options[0].option.lines[0].quantity_units == 40


async def test_real_audit_visibility_and_pagination(db):
    first = Profile(user_id=uuid4(), org_id=uuid4(), agent_id=AGENT_ID)
    other = Profile(user_id=uuid4(), org_id=first.org_id, agent_id=AGENT_ID)
    request = await db.create_request(first, "milk")
    await db.append_event(first, request.id, "request.created", "First buyer")
    await db.append_event(first, None, "agent.paused", "First buyer control")
    await db.append_event(other, None, "agent.paused", "Other buyer control")
    assert len(await db.audit(first)) == 2
    assert len(await db.audit(first, limit=1, offset=1)) == 1
    assert len(await db.audit(other)) == 1
    assert await db.request_for_order(other, UUID("00000000-0000-0000-0000-000000000003")) is None
