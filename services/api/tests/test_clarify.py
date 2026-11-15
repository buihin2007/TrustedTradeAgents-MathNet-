"""Clarification answers are read by code, so a typed date ends the loop even if the LLM ignores it."""

from datetime import date

import pytest
from tta_contracts import Criteria

from app.auth import Identity
from app.clarify import MAX_ASKS, QUESTIONS, parse_date, parse_quantity
from app.models import ParsedRequest
from app.pipeline import Pipeline
from tests.factories import DELIVERY, PROFILE, USER_ID, FakeGateway, FakeLLM, MemoryStore

TODAY = date(2026, 11, 15)  # a Sunday


@pytest.mark.parametrize(
    "text,expected",
    [
        ("2026-11-24", date(2026, 11, 24)),
        ("2026-11-17 (Tue)", date(2026, 11, 17)),
        ("24.11.", date(2026, 11, 24)),
        ("24/11/2026", date(2026, 11, 24)),
        ("deliver on 3.1.", date(2027, 1, 3)),          # past this year -> next year
        ("monday", date(2026, 11, 16)),
        ("next Friday please", date(2026, 11, 20)),
        ("perjantai", date(2026, 11, 20)),
        ("tomorrow", date(2026, 11, 16)),
        ("2026-11-01", None),                            # in the past
        ("31.02.", None),
        ("whenever", None),
    ],
)
def test_parse_date(text, expected):
    assert parse_date(text, TODAY) == expected


@pytest.mark.parametrize(
    "text,expected",
    [("20 kg", (20, "kg")), ("40 litres", (40, "l")), ("2,5 kilos", (2.5, "kg")), ("12 pcs", (12, "pcs")),
     ("lots", None), ("20 boxes", None)],
)
def test_parse_quantity(text, expected):
    assert parse_quantity(text) == expected


def no_date():
    return ParsedRequest(criteria=Criteria(query_text="chicken"), quantity=20, quantity_unit="kg")


async def round_trip(store, item, llm, answer=None):
    if answer is not None:
        question = next(q for q in (await store.request(PROFILE, item.id)).questions if q.answer is None)
        await store.answer(PROFILE, item.id, question.id, answer)
    gateway = FakeGateway()
    await Pipeline(store, gateway, llm, today=lambda: TODAY).run(item.id, PROFILE, Identity(str(USER_ID), "jwt"))
    return store.requests[item.id], gateway


async def test_typed_date_is_used_even_when_the_llm_keeps_returning_null():
    store, llm = MemoryStore(), FakeLLM(result=no_date())
    item = await store.create_request(PROFILE, "20 kg chicken")
    request, _ = await round_trip(store, item, llm)
    asked = (await store.request(PROFILE, item.id)).questions[0]
    assert request.status == "asking" and asked.question == QUESTIONS["delivery_date"]
    assert asked.choices and all(c[:4] == "2026" for c in asked.choices)   # concrete dates, no dead button

    request, gateway = await round_trip(store, item, llm, answer=DELIVERY.strftime("%d.%m.%Y"))
    assert request.status != "asking"
    registered = next(args for name, args, _ in gateway.calls if name == "register_intent")
    assert registered["delivery_date"] == DELIVERY.isoformat()
    assert registered["quantity"] == 20


async def test_known_fields_are_kept_when_a_later_parse_drops_them():
    store = MemoryStore()
    item = await store.create_request(PROFILE, "20 kg chicken")
    await round_trip(store, item, FakeLLM(result=no_date()))
    dropped_everything = ParsedRequest(criteria=Criteria(query_text="chicken"))
    request, gateway = await round_trip(store, item, FakeLLM(result=dropped_everything), answer="monday")
    registered = next(args for name, args, _ in gateway.calls if name == "register_intent")
    assert registered["quantity"] == 20 and registered["quantity_unit"] == "kg"
    assert registered["delivery_date"] == "2026-11-16"


async def test_unreadable_answers_stop_after_max_asks():
    store, llm = MemoryStore(), FakeLLM(result=no_date())
    item = await store.create_request(PROFILE, "20 kg chicken")
    request, _ = await round_trip(store, item, llm)
    for _ in range(MAX_ASKS):
        request, _ = await round_trip(store, item, llm, answer="whenever")
    assert request.status == "failed"
    assert "delivery date" in request.error
