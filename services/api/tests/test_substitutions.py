from datetime import UTC, datetime

import pytest
from tta_contracts import PriceAvailabilityResult, SubstituteGroup

from app.auth import Identity
from app.gateway import GatewayUnavailable
from app.options import build_options
from app.pipeline import Pipeline
from app.substitutions import shortage_options
from tests.factories import (
    DELIVERY,
    INTENT_ID,
    PROFILE,
    USER_ID,
    FakeGateway,
    FakeLLM,
    MemoryStore,
    hit,
    offer,
    order,
    parsed,
)


def quote(offers, quote_id="fresh-substitute-quote"):
    return PriceAvailabilityResult(
        quote_id=quote_id, delivery_date=DELIVERY, quoted_at=datetime.now(UTC), offers=offers
    )


async def test_substitution_requotes_one_basket_and_identifies_original_product():
    original = hit(1, has_substitutes=True)
    replacement = hit(2)
    gateway = FakeGateway(
        get_substitutes=[SubstituteGroup(product_id=1, substitutes=[replacement])],
        get_price_availability=quote([offer(1, available_on_date=0), offer(2)]),
    )
    updated, options = await shortage_options(
        gateway,
        parsed(),
        INTENT_ID,
        [original],
        quote([offer(1, available_on_date=0)]),
        [],
    )
    assert updated.quote_id == "fresh-substitute-quote"
    assert options[0].option.label == "split_or_substitute"
    assert options[0].option.lines[0].product_id == 2
    assert options[0].option.lines[0].substitute_for == 1
    assert [call[0] for call in gateway.calls] == ["get_substitutes", "get_price_availability"]
    assert gateway.calls[-1][1]["product_ids"] == [1, 2]


async def test_no_substitute_mapping_keeps_feasible_original_split_and_quote():
    hits = [hit(1, has_substitutes=True), hit(2)]
    prices = [offer(1, available_on_date=20), offer(2, available_on_date=20)]
    previous = quote(prices, "original")
    options = build_options(parsed(), hits, prices)
    gateway = FakeGateway(get_substitutes=[SubstituteGroup(product_id=1, substitutes=[])])
    updated, results = await shortage_options(gateway, parsed(), INTENT_ID, hits, previous, options)
    assert updated is previous
    assert results is options
    assert [call[0] for call in gateway.calls] == ["get_substitutes"]


async def test_fully_available_product_does_not_trigger_substitution():
    hits = [hit(1, has_substitutes=True)]
    previous = quote([offer(1)])
    options = build_options(parsed(), hits, previous.offers)
    gateway = FakeGateway()
    await shortage_options(gateway, parsed(), INTENT_ID, hits, previous, options)
    assert gateway.calls == []


async def test_substitution_respects_per_call_caps_and_deduplicates_products():
    hits = [hit(i, has_substitutes=True) for i in range(1, 51)]
    alternatives = [hit(i) for i in range(51, 151)]
    groups = [SubstituteGroup(product_id=i, substitutes=alternatives) for i in range(1, 11)]
    gateway = FakeGateway(get_substitutes=groups, get_price_availability=quote([offer(51)]))
    await shortage_options(gateway, parsed(), INTENT_ID, hits, quote([]), [])
    assert len(gateway.calls[0][1]["product_ids"]) == 10
    ids = gateway.calls[1][1]["product_ids"]
    assert len(ids) == len(set(ids)) == 50


async def test_existing_split_is_retained_as_an_alternative_after_fresh_quote():
    hits = [hit(1, has_substitutes=True), hit(2)]
    prices = [offer(1, available_on_date=20), offer(2, available_on_date=20)]
    options = build_options(parsed(), hits, prices)
    gateway = FakeGateway(
        get_substitutes=[SubstituteGroup(product_id=1, substitutes=[hit(3)])],
        get_price_availability=quote([*prices, offer(3)]),
    )
    _, results = await shortage_options(gateway, parsed(), INTENT_ID, hits, quote(prices), options)
    assert len(results) == 2
    assert any(line.substitute_for == 1 for item in results for line in item.option.lines)
    assert any(len(item.option.lines) == 2 for item in results)


async def test_unrelated_substitution_mapping_fails_closed():
    gateway = FakeGateway(get_substitutes=[SubstituteGroup(product_id=999, substitutes=[hit(3)])])
    with pytest.raises(GatewayUnavailable, match="unrelated"):
        await shortage_options(gateway, parsed(), INTENT_ID, [hit(1, has_substitutes=True)], quote([]), [])


async def test_pipeline_sends_fresh_quote_and_receives_substitution_hold():
    class ShortageGateway(FakeGateway):
        async def call(self, name, arguments, result_type, *, actor="agent"):
            if name == "get_price_availability":
                self.responses[name] = quote(
                    [offer(i, available_on_date=0 if i == 1 else 1000) for i in arguments["product_ids"]]
                )
            return await super().call(name, arguments, result_type, actor=actor)

    gateway = ShortageGateway(
        get_substitutes=[SubstituteGroup(product_id=1, substitutes=[hit(2)])],
        place_order=order("held", reasons=["SUBSTITUTION"]),
    )
    gateway.responses["search_products"].hits = [hit(1, has_substitutes=True)]
    store = MemoryStore()
    request = await store.create_request(PROFILE, "40 litres of milk")
    await Pipeline(store, gateway, FakeLLM()).run(request.id, PROFILE, Identity(str(USER_ID), "user-jwt"))
    assert store.requests[request.id].status == "held"
    proposal = next(args for name, args, _ in gateway.calls if name == "place_order")
    assert proposal["quote_id"] == "fresh-substitute-quote"
    assert proposal["options"][0]["lines"][0]["substitute_for"] == 1
    assert store.events[-1].details["reasons"] == ["SUBSTITUTION"]
