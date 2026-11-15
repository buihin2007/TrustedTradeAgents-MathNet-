"""The buyer agent end to end against fakes: screening, questions, menu suggestions, baskets, order."""

import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from tta_contracts import PriceAvailabilityResult, ReasonCode, ScheduleLine, SearchResult

from app.agent import Agent
from app.auth import Identity
from app.brain import Alt, Item, LLMUnavailable, Plan
from app.gateway import GatewayDenied
from tests.factories import DELIVERY, PROFILE, USER_ID, FakeGateway, FakeLLM, MemoryStore, hit, milk_plan, offer, soup

TODAY = date(2026, 11, 15)
USER = Identity(str(USER_ID), "user-jwt")


class Session:
    def __init__(self, llm=None, gateway=None, text="40 litres of milk"):
        self.store, self.llm, self.gateway = MemoryStore(), llm or FakeLLM(), gateway or FakeGateway()
        self.agent = Agent(self.store, self.gateway, self.llm, today=lambda: TODAY)
        self.text = text

    async def start(self):
        self.item = await self.store.create_request(PROFILE, self.text)
        await self.agent.run(self.item.id, PROFILE, USER)
        return self.request

    @property
    def request(self):
        return self.store.requests[self.item.id]

    @property
    def feed(self):
        return self.request.parsed["feed"]

    @property
    def question(self):
        return next(item for item in reversed(self.feed) if item["t"] == "ask")

    async def answer(self, values=(), other="", text=None):
        stored = json.dumps({"values": list(values), "other": other, "text": text or other or ", ".join(values)})
        await self.store.answer(PROFILE, self.item.id, UUID(self.question["id"]), stored)
        await self.agent.run(self.item.id, PROFILE, USER)
        return self.request

    def calls(self, name):
        return [arguments for tool, arguments, _ in self.gateway.calls if tool == name]


async def test_product_order_is_sourced_in_three_gateway_calls_and_placed_after_the_buyer_picks():
    s = Session()
    request = await s.start()
    assert request.status == "asking" and s.question["field"] == "basket"
    assert [tool for tool, _, _ in s.gateway.calls] == [
        "get_facets", "register_intent", "search_products_batch", "get_price_availability", "get_mandate",
    ]
    assert [actor for tool, _, actor in s.gateway.calls if tool in ("register_intent", "get_mandate")] == ["user", "user"]
    basket = request.parsed["baskets"][0]
    assert (basket["total"], basket["lines"][0]["units"], basket["gate"]) == ("48.00", 40, "auto")
    steps = [item for item in s.feed if item["t"] == "step"]
    assert [step["title"] for step in steps][:2] == [
        "Checking that this is a purchasing request", "Understanding your request",
    ]
    assert all(step["status"] == "done" and step["ms"] is not None for step in steps)
    assert [option["value"] for option in s.question["options"]] == ["b:0", "cancel"]
    live, _ = s.agent.hub.latest(s.item.id)
    assert live["status"] == "asking" and live["parsed"]["feed"][-1]["id"] == s.question["id"]

    request = await s.answer(values=["b:0"])
    assert request.status == "placed"
    placed = s.calls("place_order")[0]
    assert (placed["quote_id"], placed["preferred_index"]) == ("quote", 0)
    assert placed["options"][0]["lines"] == [{"product_id": 1, "quantity_units": 40.0, "substitute_for": None}]
    assert s.feed[-1] == {"t": "say", "text": s.feed[-1]["text"], "tone": "ok"}
    events = [event.event_type for event in s.store.events]
    assert events[:3] == ["request.screened", "request.parsed", "intent.registered"] and events[-1] == "order.placed"


async def test_small_talk_is_rejected_before_any_sourcing():
    s = Session(llm=FakeLLM(screen_score=0.05), text="hi, how are you?")
    request = await s.start()
    assert request.status == "rejected" and "purchasing" in request.error
    assert [tool for tool, _, _ in s.gateway.calls] == ["get_facets"]
    assert s.store.events[-1].decision == "blocked"


async def test_planner_can_reject_what_jev_let_through_unless_jev_is_sure():
    off_topic = milk_plan(on_topic=False)
    assert (await Session(llm=FakeLLM(plan=off_topic, screen_score=0.6)).start()).status == "rejected"
    assert (await Session(llm=FakeLLM(plan=off_topic, screen_score=0.95)).start()).status == "asking"


async def test_menu_without_dish_asks_people_and_date_then_suggests_dishes_from_stock():
    llm = FakeLLM(plan=Plan(kind="menu", summary="A lunch"), dishes=[soup("Salmon soup"), soup("Trout soup")])
    s = Session(llm=llm, text="I need to cook lunch for a group")
    await s.start()
    assert s.question["field"] == "headcount"
    await asyncio.sleep(0)  # the in-memory store never yields; a real one does
    assert len(llm.recipe_calls) == 1  # recipes start while the buyer is still answering
    await s.answer(values=["100"], text="100 people")
    assert s.question["field"] == "delivery_date" and len(s.question["options"]) == 5
    await s.answer(other="24.11.")
    question = s.question
    assert question["field"] == "dishes" and question["multi"]
    assert [option["value"] for option in question["options"]] == ["Salmon soup", "Trout soup"]
    card = next(item for item in s.feed if item["t"] == "dishes")["dishes"][0]
    assert card["covered"] == card["total"] == 2 and card["per_person"]
    assert s.calls("register_intent")[0]["delivery_date"] == "2026-11-24"
    assert len(s.calls("search_products_batch")[0]["queries"]) == 2  # shared ingredients are searched once

    request = await s.answer(values=["Salmon soup"])
    assert s.question["field"] == "basket"
    lines = request.parsed["baskets"][0]["lines"]
    # 100 x 120 g = 12 kg and 100 x 80 ml = 8 l, in lots of ten 1 kg / 1 l units
    assert {line["need"]: line["units"] for line in lines} == {"salmon fillet": 20, "cream": 10}
    assert len(s.calls("search_products_batch")) == 1 and len(llm.recipe_calls) == 1  # nothing is done twice
    assert next(item for item in s.feed if item["t"] == "baskets")["pantry"] == ["dill", "salt"]
    assert (await s.answer(values=["b:0"])).status == "placed"


async def test_typing_another_dish_replaces_the_suggestions():
    llm = FakeLLM(plan=Plan(kind="menu", headcount=50, delivery_date=DELIVERY), dishes=[soup("Salmon soup")])
    s = Session(llm=llm, text="what should I cook?")
    await s.start()
    assert s.question["field"] == "dishes"
    llm.dishes = [soup("Karelian stew")]
    request = await s.answer(other="Karelian stew")
    assert llm.recipe_calls[-1]["dishes"] == ["Karelian stew"]
    assert s.question["field"] == "basket" and request.parsed["menu"]["selected"] == ["Karelian stew"]


async def test_free_text_answer_updates_the_plan_through_the_model():
    llm = FakeLLM(plan=lambda text, history: milk_plan(delivery_date=DELIVERY if history else None))
    s = Session(llm=llm)
    await s.start()
    assert s.question["field"] == "delivery_date"
    await s.answer(other="the same day as usual, you know")
    assert llm.planned[-1][1] == [
        {"question": "When should it be delivered?", "answer": "the same day as usual, you know"}
    ]
    assert s.question["field"] == "basket"


async def test_missing_quantity_is_asked_with_choices_and_read_in_code():
    llm = FakeLLM(plan=milk_plan(items=[Item(name="milk", search_en="milk", unit="l")]))
    s = Session(llm=llm)
    await s.start()
    assert s.question["field"] == "quantity:0" and s.question["options"][0]["value"] == "10 l"
    request = await s.answer(other="60")
    assert request.parsed["plan"]["items"][0]["quantity"] == 60 and len(llm.planned) == 1
    assert s.question["field"] == "basket"


async def test_cancel_orders_nothing():
    s = Session()
    await s.start()
    request = await s.answer(values=["cancel"], text="Cancel")
    assert request.status == "rejected" and s.calls("place_order") == []


async def test_out_of_stock_item_shows_the_earliest_date_and_delivery_can_move_there():
    later = DELIVERY + timedelta(days=2)

    def prices(arguments):
        day = date.fromisoformat(arguments["delivery_date"])
        line = ScheduleLine(available_amount=1000, available_date=later, sales_unit="ST")
        return PriceAvailabilityResult(
            quote_id=f"quote-{day}",
            delivery_date=day,
            quoted_at=datetime.now(UTC),
            offers=[offer(1), offer(2, available_on_date=0 if day == DELIVERY else 1000, schedule=[line])],
        )

    def search(arguments):
        return [
            SearchResult(
                query_id="q", criteria_shape="", sql_source="template",
                hits=[hit(index + 1, name_en=query["query_text"])],
            )
            for index, query in enumerate(arguments["queries"])
        ]

    items = [
        Item(name="milk", search_en="milk", quantity=40, unit="l"),
        Item(name="butter", search_en="butter", quantity=10, unit="kg"),
    ]
    s = Session(
        llm=FakeLLM(plan=milk_plan(items=items)),
        gateway=FakeGateway(get_price_availability=prices, search_products_batch=search),
    )
    request = await s.start()
    basket = request.parsed["baskets"][0]
    assert [line["need"] for line in basket["lines"]] == ["milk"]
    assert basket["missing"] == [
        {"need": "butter", "essential": True, "reason": "not in stock on that date", "earliest": later.isoformat()}
    ]
    assert [option["value"] for option in s.question["options"]] == ["b:0", f"date:{later.isoformat()}", "cancel"]

    request = await s.answer(values=[f"date:{later.isoformat()}"])
    assert request.parsed["plan"]["delivery_date"] == later.isoformat()
    assert len(s.calls("register_intent")) == 2  # a new date needs a newly signed intent
    assert [line["need"] for line in request.parsed["baskets"][0]["lines"]] == ["milk", "butter"]
    assert request.parsed["quote_id"] == f"quote-{later}"


async def test_recipe_alternative_is_used_when_the_ingredient_is_out_of_stock():
    dish = soup()
    dish.ingredients[0].alt = Alt(name="trout fillet", search_en="trout fillet", note="milder")

    def search(arguments):
        return [
            SearchResult(
                query_id="q", criteria_shape="", sql_source="template",
                hits=[hit(index + 1, name_en=query["query_text"])],
            )
            for index, query in enumerate(arguments["queries"])
        ]

    gateway = FakeGateway(
        search_products_batch=search,
        get_price_availability=PriceAvailabilityResult(
            quote_id="quote",
            delivery_date=DELIVERY,
            quoted_at=datetime.now(UTC),
            offers=[offer(1, available_on_date=0), offer(2), offer(3)],
        ),
    )
    llm = FakeLLM(plan=Plan(kind="menu", headcount=100, delivery_date=DELIVERY, dishes=["Salmon soup"]), dishes=[dish])
    s = Session(llm=llm, gateway=gateway, text="salmon soup for 100")
    request = await s.start()
    assert s.question["field"] == "basket"  # the buyer named the dish: no dish question
    queries = [query["query_text"] for query in s.calls("search_products_batch")[0]["queries"]]
    assert queries == ["salmon fillet", "cooking cream", "trout fillet"]
    line = request.parsed["baskets"][0]["lines"][0]
    assert line["product_id"] == 3 and line["note"] == "Instead of salmon fillet: trout fillet (milder)"


async def test_gateway_denial_and_model_failure_end_with_a_plain_message():
    denied = Session(gateway=FakeGateway(search_products_batch=GatewayDenied(ReasonCode.AGENT_PAUSED)))
    request = await denied.start()
    assert request.status == "blocked" and "paused" in request.error

    async def broken(*args, **kwargs):
        raise LLMUnavailable("The language model did not give a usable answer (TimeoutError). Please try again.")

    llm = FakeLLM()
    llm.plan = broken
    request = await Session(llm=llm).start()
    assert request.status == "failed" and "language model" in request.error
    assert [item["status"] for item in request.parsed["feed"] if item["t"] == "step"] == ["done", "fail"]


async def test_nothing_in_stock_asks_for_another_date_instead_of_suggesting_dishes():
    gateway = FakeGateway(
        get_price_availability=lambda arguments: PriceAvailabilityResult(
            quote_id="q",
            delivery_date=date.fromisoformat(arguments["delivery_date"]),
            quoted_at=datetime.now(UTC),
            offers=[offer(1, available_on_date=0 if arguments["delivery_date"] == DELIVERY.isoformat() else 500)],
        )
    )
    llm = FakeLLM(plan=Plan(kind="menu", headcount=50, delivery_date=DELIVERY), dishes=[soup()])
    s = Session(llm=llm, gateway=gateway, text="dinner for 50")
    await s.start()
    assert s.question["field"] == "delivery_date"
    assert "no stock" in next(item for item in s.feed if item["t"] == "say")["text"]
    assert not any(item["t"] == "dishes" for item in s.feed)
    await s.answer(values=[(DELIVERY + timedelta(days=1)).isoformat()])
    assert s.question["field"] == "dishes"
    assert len(s.calls("register_intent")) == 2


async def test_suggest_other_dishes_avoids_what_was_missing():
    def search(arguments):
        return [
            SearchResult(
                query_id="q", criteria_shape="", sql_source="template",
                hits=[] if "salmon" in query["query_text"] else [hit(1, name_en=query["query_text"])],
            )
            for query in arguments["queries"]
        ]

    llm = FakeLLM(plan=Plan(kind="menu", headcount=50, delivery_date=DELIVERY), dishes=[soup()])
    s = Session(llm=llm, gateway=FakeGateway(search_products_batch=search), text="dinner for 50")
    await s.start()
    assert s.question["options"][-1]["value"] == "more"
    await s.answer(values=["more"], text="Suggest other dishes")
    assert llm.recipe_calls[-1]["exclude"] == ["Salmon soup"]
    assert llm.recipe_calls[-1]["unavailable"] == ["salmon fillet"]
    assert s.question["field"] == "dishes"
