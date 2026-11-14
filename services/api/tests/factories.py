from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from pydantic import TypeAdapter
from tta_contracts import (
    ActorType,
    AuditDecision,
    AuditEvent,
    Criteria,
    Facets,
    Offer,
    Order,
    PriceAvailabilityResult,
    ProductHit,
    SearchResult,
)

from app.models import ParsedRequest, Profile, Question, RequestDetail
from app.store import Conflict

USER_ID = UUID("00000000-0000-0000-0000-000000000001")
ORG_ID = UUID("00000000-0000-0000-0000-00000000000a")
AGENT_ID = UUID("00000000-0000-0000-0000-0000000000a1")
INTENT_ID = "00000000-0000-0000-0000-000000000002"
ORDER_ID = "00000000-0000-0000-0000-000000000003"
PROFILE = Profile(user_id=USER_ID, org_id=ORG_ID, agent_id=AGENT_ID)
DELIVERY = date(2026, 11, 17)


def parsed(**kwargs):
    return ParsedRequest(
        criteria=Criteria(query_text="milk"),
        quantity=40,
        quantity_unit="l",
        delivery_date=DELIVERY,
        **kwargs,
    )


def hit(product_id=1, **kwargs):
    values = dict(
        product_id=product_id,
        name_en="Milk",
        name_fi="Maito",
        brand="Valio",
        vendor_name="Valio",
        category_code="milk",
        category_name="Milk",
        sales_unit="ST",
        unit_volume_l=1,
        unit_weight_kg=1,
        allowed_lot_size=10,
        temperature_condition="cold",
        delivery_reliability=0.9,
        co2_kg_per_kg=1,
        rrf_score=0.5,
    )
    return ProductHit(**{**values, **kwargs})


def offer(product_id=1, **kwargs):
    values = dict(
        product_id=product_id,
        unit_price_eur=Decimal("1.20"),
        unit_price_with_tax_eur=None,
        comparison_price_eur=Decimal("1.20"),
        comparison_unit="L",
        logistic_model="Stock",
        available_on_date=1000,
    )
    return Offer(**{**values, **kwargs})


def order(status="placed", **kwargs):
    values = dict(
        order_id=ORDER_ID,
        intent_id=INTENT_ID,
        status=status,
        options=[],
        total_eur=Decimal("48"),
        delivery_date=DELIVERY,
        created_at=datetime.now(UTC),
        reasons=[],
    )
    return Order(**{**values, **kwargs})


class MemoryStore:
    """Test double only. Production always uses PostgresStore."""

    def __init__(self):
        self.profiles = {str(USER_ID): PROFILE}
        self.requests = {}
        self.events = []

    async def profile(self, user_id):
        return self.profiles.get(str(user_id))

    async def create_request(self, profile, text):
        now = datetime.now(UTC)
        item = RequestDetail(
            id=uuid4(),
            user_id=profile.user_id,
            org_id=profile.org_id,
            text=text,
            status="running",
            step="queued",
            created_at=now,
            updated_at=now,
        )
        self.requests[item.id] = item
        return item.model_copy(deep=True)

    async def request(self, profile, request_id):
        item = self.requests.get(request_id)
        if item and item.user_id == profile.user_id and item.org_id == profile.org_id:
            return item.model_copy(deep=True)
        return None

    async def list_requests(self, profile, limit=50, offset=0, status=None):
        items = [
            r.model_copy(deep=True)
            for r in self.requests.values()
            if r.user_id == profile.user_id and r.org_id == profile.org_id and (status is None or r.status == status)
        ]
        return items[offset : offset + limit]

    async def request_for_order(self, profile, order_id):
        for item in await self.list_requests(profile):
            if item.order_id == order_id:
                return item
        return None

    async def update_request(self, profile, request_id, **kwargs):
        item = await self.request(profile, request_id)
        if item:
            self.requests[request_id] = RequestDetail.model_validate(
                {
                    **item.model_dump(),
                    **kwargs,
                }
            )

    async def save_options(self, profile, request_id, options):
        await self.update_request(profile, request_id, options=options)

    async def ask(self, profile, request_id, question, choices):
        item = self.requests[request_id]
        item.questions.append(
            Question(
                id=uuid4(),
                request_id=request_id,
                question=question,
                choices=choices,
                asked_at=datetime.now(UTC),
            )
        )
        await self.update_request(profile, request_id, status="asking")

    async def answer(self, profile, request_id, question_id, answer):
        item = self.requests[request_id]
        if item.status != "asking":
            raise Conflict("Request is not waiting for an answer")
        for question in item.questions:
            if question.id == question_id and question.answer is None:
                question.answer = answer
                question.answered_at = datetime.now(UTC)
                item.status = "running"
                return
        raise Conflict("Question is already answered or belongs to another request")

    async def record_order(self, profile, request_id, result, *, audit=None):
        before = self.requests[request_id].model_copy(deep=True)
        try:
            await self.update_request(
                profile,
                request_id,
                order_id=UUID(result.order_id),
                status=result.status,
                options=result.options,
                error=None,
                step="waiting_for_approval" if result.status == "held" else "complete",
            )
            if audit:
                await self.append_event(profile, request_id, **audit)
        except Exception:
            self.requests[request_id] = before
            raise

    async def append_event(
        self,
        profile,
        request_id,
        event_type,
        summary,
        decision=AuditDecision.NONE,
        actor=ActorType.AGENT,
        details=None,
        trace_id=None,
    ):
        event = AuditEvent(
            seq=len(self.events) + 1,
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
            trace_id=trace_id,
        )
        event.prev_hash = self.events[-1].hash if self.events else None
        event.hash = event.compute_hash(event.prev_hash)
        self.events.append(event)
        return event

    async def audit(self, profile, request_id=None, decision=None, limit=50, offset=0):
        return [
            e
            for e in self.events
            if e.details["user_id"] == str(profile.user_id)
            and (request_id is None or e.request_id == str(request_id))
            and (decision is None or e.decision == decision)
        ][offset : offset + limit]


class FakeLLM:
    def __init__(self, result=None):
        self.result = result or parsed()
        self.answers = []

    async def parse(self, text, answers, facets):
        self.answers = answers
        return self.result

    async def explain(self, options):
        return [f"Estimated EUR {item.total_eur}" for item in options]


class FakeGateway:
    def __init__(self, **responses):
        self.calls = []
        self.responses = {
            "get_facets": Facets(claims=[], allergens=[], categories=[], temperatures=[], sales_units=[]),
            "register_intent": dict(
                intent_id=INTENT_ID,
                org_id=str(ORG_ID),
                user_id=str(USER_ID),
                request_text="milk",
                criteria=Criteria(query_text="milk"),
                quantity=40,
                quantity_unit="l",
                delivery_date=DELIVERY,
                created_at=datetime.now(UTC),
                expires_at=datetime.now(UTC),
            ),
            "search_products": SearchResult(query_id="query", criteria_shape="", sql_source="template", hits=[hit()]),
            "get_price_availability": PriceAvailabilityResult(
                quote_id="quote",
                delivery_date=DELIVERY,
                quoted_at=datetime.now(UTC),
                offers=[offer()],
            ),
            "place_order": order(),
            "get_order": order(),
            **responses,
        }

    @asynccontextmanager
    async def session(self, user):
        yield self

    async def call(self, name, arguments, result_type, *, actor="agent"):
        self.calls.append((name, arguments, actor))
        value = self.responses[name]
        if isinstance(value, Exception):
            raise value
        if callable(value):
            value = value(arguments)
        return TypeAdapter(result_type).validate_python(value)
