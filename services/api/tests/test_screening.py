"""Jev screens the buyer's first message before any LLM parse or gateway call."""

from app.auth import Identity
from app.pipeline import Pipeline
from tests.factories import PROFILE, USER_ID, FakeGateway, FakeLLM, MemoryStore


async def run(text, llm):
    store = MemoryStore()
    item = await store.create_request(PROFILE, text)
    gateway = FakeGateway()
    await Pipeline(store, gateway, llm).run(item.id, PROFILE, Identity(str(USER_ID), "user-jwt"))
    return store, store.requests[item.id], gateway


async def test_small_talk_is_rejected_without_touching_the_gateway():
    llm = FakeLLM(screen_score=0.03)
    store, request, gateway = await run("hi, how are you today?", llm)
    assert request.status == "rejected"
    assert "purchasing request" in request.error
    assert gateway.calls == []
    assert llm.answers == []                      # parse never ran
    screened = [e for e in store.events if e.event_type == "request.screened"]
    assert screened[0].decision == "blocked" and screened[0].details["jev_score"] == 0.03


async def test_unavailable_screen_fails_open():
    store, request, gateway = await run("40 litres of milk", FakeLLM(screen_score=None))
    assert request.status != "rejected"
    assert gateway.calls
    assert any(e.summary == "Screen unavailable, request accepted" for e in store.events)


async def test_answers_to_questions_are_not_screened_again():
    llm = FakeLLM(screen_score=0.0)               # would reject if screened
    store = MemoryStore()
    item = await store.create_request(PROFILE, "milk")
    await store.ask(PROFILE, item.id, "How much?", [])
    question = (await store.request(PROFILE, item.id)).questions[0]
    await store.answer(PROFILE, item.id, question.id, "40 litres")
    await Pipeline(store, FakeGateway(), llm).run(item.id, PROFILE, Identity(str(USER_ID), "user-jwt"))
    assert llm.screened == []
    assert store.requests[item.id].status != "rejected"
