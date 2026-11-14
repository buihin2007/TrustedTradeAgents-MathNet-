"""Buyer-only SQL. A small pool is shared by REST requests and background tasks."""

from datetime import UTC, datetime
from uuid import UUID

from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from tta_contracts import ActorType, AuditDecision, AuditEvent, Order

from .models import OptionPreview, Profile, Question, RequestDetail, RequestRecord
from .telemetry import current_trace_id


class Conflict(Exception):
    pass


class PostgresStore:
    def __init__(self, database_url: str):
        self.pool = AsyncConnectionPool(
            database_url,
            min_size=0,
            max_size=2,
            open=False,
            timeout=5,
            kwargs={"row_factory": dict_row, "prepare_threshold": None},
        )

    async def open(self):
        await self.pool.open()

    async def close(self):
        await self.pool.close()

    async def profile(self, user_id: str) -> Profile | None:
        async with self.pool.connection() as conn:
            row = await (
                await conn.execute("SELECT * FROM buyer.profiles WHERE user_id = %s", (UUID(user_id),))
            ).fetchone()
        return Profile.model_validate(row) if row else None

    async def create_request(self, profile: Profile, text: str) -> RequestRecord:
        async with self.pool.connection() as conn:
            row = await (
                await conn.execute(
                    "INSERT INTO buyer.requests (user_id, org_id, text, step) "
                    "VALUES (%s, %s, %s, 'queued') RETURNING *",
                    (profile.user_id, profile.org_id, text),
                )
            ).fetchone()
        return RequestRecord.model_validate(row)

    async def list_requests(self, profile: Profile, limit=50, offset=0, status=None):
        async with self.pool.connection() as conn:
            rows = await (
                await conn.execute(
                    "SELECT * FROM buyer.requests WHERE user_id = %s AND org_id = %s "
                    "AND (%s::text IS NULL OR status = %s) "
                    "ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s",
                    (profile.user_id, profile.org_id, status, status, limit, offset),
                )
            ).fetchall()
        return [RequestRecord.model_validate(row) for row in rows]

    async def request(self, profile: Profile, request_id: UUID) -> RequestDetail | None:
        async with self.pool.connection() as conn:
            row = await (
                await conn.execute(
                    "SELECT * FROM buyer.requests WHERE id = %s AND user_id = %s AND org_id = %s",
                    (request_id, profile.user_id, profile.org_id),
                )
            ).fetchone()
            if not row:
                return None
            options = await (
                await conn.execute(
                    "SELECT payload FROM buyer.request_options WHERE request_id = %s ORDER BY idx",
                    (request_id,),
                )
            ).fetchall()
            questions = await (
                await conn.execute(
                    "SELECT * FROM buyer.questions WHERE request_id = %s ORDER BY asked_at, id",
                    (request_id,),
                )
            ).fetchall()
        return RequestDetail.model_validate(
            {
                **row,
                "options": [item["payload"] for item in options],
                "questions": questions,
            }
        )

    async def request_for_order(self, profile: Profile, order_id: UUID):
        async with self.pool.connection() as conn:
            row = await (
                await conn.execute(
                    "SELECT id FROM buyer.requests WHERE order_id = %s AND user_id = %s AND org_id = %s",
                    (order_id, profile.user_id, profile.org_id),
                )
            ).fetchone()
        return await self.request(profile, row["id"]) if row else None

    async def update_request(self, profile: Profile, request_id: UUID, **fields):
        permitted = {"status", "step", "parsed", "intent_id", "order_id", "error", "trace_id"}
        if not fields or not fields.keys() <= permitted:
            raise ValueError("Unsupported request update")
        values = []
        for name, value in fields.items():
            values.append(Jsonb(value) if name == "parsed" and value is not None else value)
        query = sql.SQL(
            "UPDATE buyer.requests SET {}, updated_at = now() WHERE id = %s AND user_id = %s AND org_id = %s"
        ).format(sql.SQL(", ").join(sql.SQL("{} = %s").format(sql.Identifier(k)) for k in fields))
        async with self.pool.connection() as conn:
            await conn.execute(query, (*values, request_id, profile.user_id, profile.org_id))

    async def save_options(self, profile: Profile, request_id: UUID, options: list, *, connection=None):
        async def write(conn):
            owned = await (
                await conn.execute(
                    "SELECT id FROM buyer.requests WHERE id = %s AND user_id = %s AND org_id = %s FOR UPDATE",
                    (request_id, profile.user_id, profile.org_id),
                )
            ).fetchone()
            if not owned:
                raise Conflict("Request is unavailable")
            await conn.execute("DELETE FROM buyer.request_options WHERE request_id = %s", (request_id,))
            for index, item in enumerate(options):
                option = item.option if isinstance(item, OptionPreview) else item
                await conn.execute(
                    "INSERT INTO buyer.request_options (request_id, idx, label, payload, explanation) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (
                        request_id,
                        index,
                        option.label,
                        Jsonb(item.model_dump(mode="json")),
                        option.explanation,
                    ),
                )

        if connection is not None:
            await write(connection)
        else:
            async with self.pool.connection() as conn:
                await write(conn)

    async def ask(self, profile: Profile, request_id: UUID, question: str, choices: list[str]):
        async with self.pool.connection() as conn:
            owned = await (
                await conn.execute(
                    "UPDATE buyer.requests SET status='asking', step='waiting_for_answer', updated_at=now() "
                    "WHERE id=%s AND user_id=%s AND org_id=%s RETURNING id",
                    (request_id, profile.user_id, profile.org_id),
                )
            ).fetchone()
            if not owned:
                raise Conflict("Request is unavailable")
            row = await (
                await conn.execute(
                    "INSERT INTO buyer.questions (request_id, question, choices) VALUES (%s, %s, %s) RETURNING *",
                    (request_id, question, Jsonb(choices)),
                )
            ).fetchone()
        return Question.model_validate(row)

    async def answer(self, profile: Profile, request_id: UUID, question_id: UUID, answer: str):
        async with self.pool.connection() as conn:
            row = await (
                await conn.execute(
                    "SELECT status FROM buyer.requests WHERE id=%s AND user_id=%s AND org_id=%s FOR UPDATE",
                    (request_id, profile.user_id, profile.org_id),
                )
            ).fetchone()
            if not row or row["status"] != "asking":
                raise Conflict("Request is not waiting for an answer")
            question = await (
                await conn.execute(
                    "UPDATE buyer.questions SET answer=%s, answered_at=now() "
                    "WHERE id=%s AND request_id=%s AND answer IS NULL RETURNING id",
                    (answer, question_id, request_id),
                )
            ).fetchone()
            if not question:
                raise Conflict("Question was already answered or does not belong to this request")
            await conn.execute(
                "UPDATE buyer.requests SET status='running', step='queued', error=NULL, updated_at=now() WHERE id=%s",
                (request_id,),
            )

    async def record_order(self, profile: Profile, request_id: UUID, order: Order):
        async with self.pool.connection() as conn:
            await self.save_options(profile, request_id, order.options, connection=conn)
            await conn.execute(
                "UPDATE buyer.requests SET order_id=%s, status=%s, step=%s, error=NULL, updated_at=now() "
                "WHERE id=%s AND user_id=%s AND org_id=%s",
                (
                    UUID(order.order_id),
                    order.status.value,
                    "complete" if order.status != "held" else "waiting_for_approval",
                    request_id,
                    profile.user_id,
                    profile.org_id,
                ),
            )

    async def append_event(
        self,
        profile: Profile,
        request_id: UUID | None,
        event_type: str,
        summary: str,
        decision=AuditDecision.NONE,
        actor=ActorType.AGENT,
        details=None,
        trace_id=None,
    ):
        event = AuditEvent(
            ts=datetime.now(UTC),
            request_id=str(request_id) if request_id else None,
            actor_type=actor,
            event_type=event_type,
            decision=decision,
            summary=summary,
            details={
                **(details or {}),
                "user_id": str(profile.user_id),
                "org_id": str(profile.org_id),
            },
            trace_id=trace_id or current_trace_id(),
        )
        async with self.pool.connection() as conn:
            # The schema has a single global chain. Serialize head-read + insertion
            # across workers/connections, including events for different buyers.
            await conn.execute("SELECT pg_advisory_xact_lock(20261115, 3)")
            previous = await (
                await conn.execute("SELECT hash FROM buyer.audit_events ORDER BY seq DESC LIMIT 1")
            ).fetchone()
            event.prev_hash = previous["hash"] if previous else None
            event.hash = event.compute_hash(event.prev_hash)
            row = await (
                await conn.execute(
                    "INSERT INTO buyer.audit_events "
                    "(ts, request_id, actor_type, event_type, decision, summary, details, "
                    "trace_id, prev_hash, hash) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING seq",
                    (
                        event.ts,
                        request_id,
                        event.actor_type.value,
                        event.event_type,
                        event.decision.value,
                        event.summary,
                        Jsonb(event.details),
                        event.trace_id,
                        event.prev_hash,
                        event.hash,
                    ),
                )
            ).fetchone()
            event.seq = row["seq"]
        return event

    async def audit(self, profile: Profile, request_id=None, decision=None, limit=50, offset=0):
        async with self.pool.connection() as conn:
            rows = await (
                await conn.execute(
                    "SELECT a.* FROM buyer.audit_events a LEFT JOIN buyer.requests r ON r.id=a.request_id "
                    "WHERE ((r.user_id=%s AND r.org_id=%s) OR (a.request_id IS NULL "
                    "AND a.details->>'user_id'=%s AND a.details->>'org_id'=%s)) "
                    "AND (%s::uuid IS NULL OR a.request_id=%s) "
                    "AND (%s::text IS NULL OR a.decision=%s) ORDER BY a.seq LIMIT %s OFFSET %s",
                    (
                        profile.user_id,
                        profile.org_id,
                        str(profile.user_id),
                        str(profile.org_id),
                        request_id,
                        request_id,
                        decision,
                        decision,
                        limit,
                        offset,
                    ),
                )
            ).fetchall()
        return [
            AuditEvent.model_validate({**row, "request_id": str(row["request_id"]) if row["request_id"] else None})
            for row in rows
        ]
