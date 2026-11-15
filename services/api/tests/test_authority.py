"""Autonomy within the mandate, splitting across products, Valio substitute pairs, signed quotes."""

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from tta_contracts import (
    Mandate,
    PriceAvailabilityResult,
    SearchResult,
    SubstituteGroup,
    quote_signing_bytes,
)

from app.agent import Agent
from app.auth import Identity
from app.brain import Item
from app.sourcing import Need, candidates, footprint, split
from app.trust import QuoteVerifier
from tests.factories import (
    AGENT_ID,
    DELIVERY,
    ORG_ID,
    PROFILE,
    USER_ID,
    FakeGateway,
    FakeLLM,
    MemoryStore,
    hit,
    milk_plan,
    offer,
)

USER = Identity(str(USER_ID), "jwt")


def mandate(auto="300", order="3000"):
    return Mandate(mandate_id="m", org_id=str(ORG_ID), agent_id=str(AGENT_ID), version=2, signed_by=str(USER_ID),
                   signed_at=datetime.now(UTC), auto_limit_eur=Decimal(auto), order_limit_eur=Decimal(order),
                   weekly_budget_eur=Decimal("10000"))


async def run(llm, gateway, verifier=None):
    store = MemoryStore()
    item = await store.create_request(PROFILE, "milk")
    agent = Agent(store, gateway, llm, today=lambda: date(2026, 11, 15), verifier=verifier)
    await agent.run(item.id, PROFILE, USER)
    return store, store.requests[item.id], gateway


def named_search(stock_ids=None):
    def search(arguments):
        return [
            SearchResult(query_id="q", criteria_shape="", sql_source="template",
                         hits=[hit(product_id, name_en=query["query_text"]) for product_id in (stock_ids or [1])])
            for query in arguments["queries"]
        ]
    return search


async def test_within_authority_the_agent_orders_without_asking(monkeypatch):
    monkeypatch.setenv("AGENT_AUTO_ORDER", "1")
    _, request, gateway = await run(FakeLLM(), FakeGateway(get_mandate=mandate()))
    assert request.status == "placed"
    assert not any(item["t"] == "ask" for item in request.parsed["feed"])
    assert "without asking" in next(item["text"] for item in request.parsed["feed"] if item["t"] == "say")
    assert [tool for tool, _, _ in gateway.calls][-1] == "place_order"


async def test_above_the_auto_limit_the_agent_asks_and_the_example_request_is_not_blocked(monkeypatch):
    monkeypatch.setenv("AGENT_AUTO_ORDER", "1")
    plan = milk_plan(items=[Item(name="lactose-free milk", search_en="lactose free milk", quantity=1000, unit="l")])
    _, request, _ = await run(FakeLLM(plan=plan), FakeGateway(get_mandate=mandate()))
    basket = request.parsed["baskets"][0]
    assert basket["total"] == "1200.00" and basket["gate"] == "approval"   # 1,000 l x 1.20 EUR
    assert request.status == "asking"


def test_split_covers_the_amount_with_several_products():
    need = Need(key="n0", name="milk", search_en="milk", search_fi=None, amount=Decimal(1000), unit="l")
    options = candidates(need, [hit(1), hit(2), hit(3)], {
        1: offer(1, available_on_date=600), 2: offer(2, available_on_date=300), 3: offer(3, available_on_date=900),
    })
    picks = split(options, "best_match", need)
    assert [(p.hit.product_id, p.units) for p in picks] == [(1, 600), (2, 300), (3, 100)]
    assert split(options[:2], "best_match", need) is None   # 900 l is not enough


async def test_short_item_uses_a_valio_substitute_pair_and_the_basket_needs_approval(monkeypatch):
    monkeypatch.setenv("AGENT_AUTO_ORDER", "1")

    def prices(arguments):
        ids = arguments["product_ids"]
        return PriceAvailabilityResult(
            quote_id=f"quote-{len(ids)}", delivery_date=DELIVERY, quoted_at=datetime.now(UTC),
            offers=[offer(i, available_on_date=0 if i == 1 else 900) for i in ids],
        )

    gateway = FakeGateway(
        get_mandate=mandate(),
        search_products_batch=named_search(),
        get_price_availability=prices,
        get_substitutes=lambda arguments: [SubstituteGroup(product_id=1, substitutes=[hit(7, name_en="Milk 1 l")])],
    )
    _, request, gateway = await run(FakeLLM(), gateway)
    line = request.parsed["baskets"][0]["lines"][0]
    assert (line["product_id"], line["substitute_for"]) == (7, 1)
    assert request.parsed["baskets"][0]["gate"] == "approval" and request.status == "asking"
    assert request.parsed["quote_id"] == "quote-2"   # one quote covers the original and the substitute
    tools = [tool for tool, _, _ in gateway.calls]
    assert tools.count("get_price_availability") == 2 and "get_substitutes" in tools


def test_footprint_sums_co2_and_weights_reliability_by_cost():
    lines = [{"co2_kg": 10.0, "reliability": 1.0, "line_total": "30"}, {"co2_kg": None, "reliability": 0.5, "line_total": "10"}]
    assert footprint(lines) == (10.0, 0.875)


async def test_signed_quotes_are_verified_and_tampering_is_detected():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from app.trust import _bytes  # noqa: F401  (decoder used by the verifier)

    key = Ed25519PrivateKey.generate()
    verifier = QuoteVerifier("http://nginx/mcp")
    verifier.keys, verifier.fetched = {"k": key.public_key()}, 1e18
    quote = PriceAvailabilityResult(quote_id="q", delivery_date=DELIVERY, quoted_at=datetime.now(UTC),
                                    offers=[offer(1)], valid_until=datetime.now(UTC) + timedelta(minutes=15), key_id="k")
    import base64
    quote.signature = base64.urlsafe_b64encode(key.sign(quote_signing_bytes(quote))).rstrip(b"=").decode()
    # what an agent receives: the tool result as JSON
    received = PriceAvailabilityResult.model_validate(json.loads(quote.model_dump_json()))
    assert (await verifier.check(received))[0] is True
    received.offers[0].unit_price_eur = Decimal("0.10")
    trusted, sentence = await verifier.check(received)
    assert trusted is False and "altered" in sentence


@pytest.mark.parametrize("auto", ["0", "1"])
async def test_valio_substitute_is_never_ordered_automatically(monkeypatch, auto):
    monkeypatch.setenv("AGENT_AUTO_ORDER", auto)
    gateway = FakeGateway(
        get_mandate=mandate(),
        search_products_batch=named_search(),
        get_price_availability=lambda arguments: PriceAvailabilityResult(
            quote_id="q", delivery_date=DELIVERY, quoted_at=datetime.now(UTC),
            offers=[offer(i, available_on_date=0 if i == 1 else 900) for i in arguments["product_ids"]]),
        get_substitutes=lambda arguments: [SubstituteGroup(product_id=1, substitutes=[hit(7, name_en="Milk")])],
    )
    _, request, gateway = await run(FakeLLM(), gateway)
    assert all(tool != "place_order" for tool, _, _ in gateway.calls)
    assert UUID(str(request.id))
