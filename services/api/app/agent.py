"""The buyer agent: understand -> ask -> source -> baskets -> order.

One LLM call understands the request while Jev screens it. Code decides what is still missing and asks
one multiple-choice question at a time. Sourcing is one batched catalog search plus one price and
stock call. Baskets are computed in code and the gateway decides placed / held / blocked.
Every step is published to the browser the moment it happens.
"""

import asyncio
import json
import logging
import re
from contextlib import asynccontextmanager
from datetime import date, timedelta
from time import monotonic
from uuid import UUID, uuid4

from opentelemetry import trace
from tta_contracts import (
    ActorType,
    AuditDecision,
    Criteria,
    Facets,
    Intent,
    Mandate,
    Must,
    Order,
    OrderLineIn,
    OrderOption,
    PlaceOrderRequest,
    PriceAvailabilityResult,
    RegisterIntentRequest,
    SearchResult,
)

from .auth import Identity
from .brain import Dish, LLMUnavailable, Plan, _number
from .clarify import parse_date, parse_quantity
from .gateway import GatewayDenied, GatewayUnavailable
from .live import FINAL, Hub
from .sourcing import (
    build_baskets,
    criteria,
    dish_cards,
    missing_report,
    needs_from_dishes,
    needs_from_items,
    pick,
)
from .telemetry import current_trace_id, io

logger = logging.getLogger(__name__)
tracer = trace.get_tracer("tta.api")

SCREEN_BLOCK = 0.2  # Jev at or below this: rejected at once
SCREEN_TRUST = 0.9  # Jev at or above this overrules a planner that calls the message off topic
MAX_ASKS = {"basket": 6, "dishes": 6}
MAX_QUERIES = 30
OFF_TOPIC = (
    "I can only help with purchasing for your kitchen: ordering products, planning dishes or menus and "
    "sourcing their ingredients from Valio Aimo. Try “20 kg chicken fillet for Friday” or "
    "“Lunch for 80 people next Tuesday, nut-free”."
)
REASONS = {
    "FORBIDDEN": "the credentials were not accepted",
    "AGENT_PAUSED": "the purchasing agent is paused (resume it on the Mandate page)",
    "INTENT_INVALID": "the registered request expired or does not match this order",
    "MUST_VIOLATED": "a product is outside what you asked for or outside your mandate",
    "CAP_EXCEEDED": "the agent asked for too many products at once",
    "RATE_LIMIT": "too many calls in a short time, try again in a minute",
    "LOT_SIZE": "a quantity does not match the product's lot size",
    "SUBSTITUTION": "it contains a substitute",
    "PARTIAL_AVAILABILITY": "price or stock could not be confirmed for every line",
    "PRICE_MOVED": "a price moved since the quote",
    "OVER_AUTO_LIMIT": "the total is above your auto-approval limit",
    "OVER_ORDER_LIMIT": "the total is above your per-order limit",
    "OVER_WEEKLY_BUDGET": "it would exceed your weekly budget",
    "JEV_BLOCK": "the gateway's judge found it does not match your request",
    "JEV_UNSURE": "the gateway's judge was not sure it matches your request",
    "JEV_UNAVAILABLE": "the gateway's judge was unavailable",
}
BLOCKING = {"MUST_VIOLATED", "OVER_ORDER_LIMIT", "OVER_WEEKLY_BUDGET", "LOT_SIZE", "JEV_BLOCK"}
DIET_ALLERGENS = {
    "vegan": ("AM", "AE", "AF", "AC", "UM"),
    "vegetarian": ("AF", "AC", "UM"),
    "gluten_free": ("AW", "UW", "NR", "GB", "GS", "GK"),
}


def reasons_text(reasons) -> str:
    texts = [REASONS.get(getattr(r, "value", r), str(getattr(r, "value", r))) for r in reasons if r]
    return "; ".join(texts) or "no reason was given"


def fmt_day(day: date, today: date) -> str:
    label = f"{day.strftime('%a')} {day.day} {day.strftime('%b')}"
    return f"Tomorrow, {label}" if day == today + timedelta(days=1) else label


def date_options(today: date) -> list[dict]:
    options, day = [], today
    while len(options) < 5:
        day += timedelta(days=1)
        if day.weekday() == 6:  # no Sunday deliveries
            continue
        desc = "Order cut-off may already apply" if day == today + timedelta(days=1) else ""
        options.append({"value": day.isoformat(), "label": fmt_day(day, today), "desc": desc})
    return options


def slot_question(plan: Plan, today: date) -> dict | None:
    """The next thing the buyer has to tell us, as one multiple-choice question."""
    if plan.kind == "menu" and plan.headcount is None:
        return {
            "field": "headcount",
            "header": "People",
            "text": "How many people are you cooking for?",
            "options": [{"value": str(n), "label": f"{n} people", "desc": ""} for n in (20, 50, 100, 200)],
            "hint": "Another number, e.g. 80",
        }
    for index, item in enumerate(plan.items):
        if item.quantity is None or item.unit is None:
            sizes = {
                "kg": ("5 kg", "10 kg", "20 kg", "50 kg"),
                "l": ("10 l", "20 l", "40 l", "100 l"),
                "pcs": ("10 pcs", "50 pcs", "100 pcs"),
            }.get(item.unit, ("5 kg", "10 kg", "20 l", "50 pcs"))
            return {
                "field": f"quantity:{index}",
                "header": "Quantity",
                "text": f"How much {item.name} do you need?",
                "options": [{"value": size, "label": size, "desc": ""} for size in sizes],
                "hint": "Another amount, e.g. 12 kg",
            }
    if plan.delivery_date is None:
        return {
            "field": "delivery_date",
            "header": "Delivery",
            "text": "When should it be delivered?",
            "options": date_options(today),
            "hint": "Another date, e.g. 24.11. or Friday",
        }
    return None


def decode(raw: str) -> tuple[list[str], str, str]:
    """(option values, free text, text to show) of a stored answer."""
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and isinstance(data.get("values"), list):
            other = str(data.get("other") or "")
            return [str(v) for v in data["values"]], other, str(data.get("text") or other)
    except ValueError:
        pass
    return [], raw, raw


def avoided(plan: Plan, allergens: dict) -> list[str]:
    codes = set(plan.avoid_allergens)
    for diet in plan.diet:
        codes |= set(DIET_ALLERGENS.get(diet, ()))
    return sorted(code for code in codes if code in allergens)


class Run:
    """One pass of the agent over a request: live state, feed and deferred persistence."""

    def __init__(self, agent: "Agent", request, profile):
        self.agent = agent
        self.request = request
        self.profile = profile
        saved = request.parsed if isinstance(request.parsed, dict) and request.parsed.get("v") == 2 else None
        self.state = saved or {"v": 2, "feed": [], "history": [], "asked": {}}
        self.status = "running"
        self.step_name = "thinking"
        self.error = None
        self.events: list[dict] = []
        self.question: dict | None = None
        self.gateway = None
        self.trace_id = request.trace_id

    @property
    def feed(self) -> list[dict]:
        return self.state["feed"]

    def push(self, step: str | None = None) -> None:
        if step:
            self.step_name = step
        snapshot = {
            "id": str(self.request.id),
            "text": self.request.text,
            "status": self.status,
            "step": self.step_name,
            "error": self.error,
            "parsed": self.state,
            "created_at": self.request.created_at.isoformat(),
        }
        self.agent.hub.publish(self.request.id, json.loads(json.dumps(snapshot, default=str)))

    @asynccontextmanager
    async def step(self, title: str):
        item = {"t": "step", "title": title, "status": "run", "detail": "", "items": [], "ms": None}
        self.feed.append(item)
        self.push(title)
        started = monotonic()
        with self.agent.tracer.start_as_current_span(f"step: {title}", record_exception=False) as span:
            try:
                yield item
                item["status"] = "done"
            except BaseException:
                item["status"] = "fail"
                raise
            finally:
                item["ms"] = int((monotonic() - started) * 1000)
                io(span, output={"status": item["status"], "detail": item["detail"], "items": item["items"]})
                self.push()

    def say(self, text: str, tone: str = "info") -> None:
        self.feed.append({"t": "say", "text": text, "tone": tone})

    def audit(self, event_type, summary, decision=AuditDecision.NONE, actor=ActorType.AGENT, details=None) -> None:
        self.events.append(
            {"event_type": event_type, "summary": summary, "decision": decision, "actor": actor, "details": details}
        )

    async def finish(self, status: str, message: str, tone: str = "info") -> None:
        self.status = status
        self.error = message if status in ("failed", "blocked", "rejected") else None
        self.say(message, tone)
        self.push("complete")

    async def flush(self) -> None:
        """Write the question, the state and the queued audit events. The browser already has them."""
        store, profile, request_id = self.agent.store, self.profile, self.request.id
        try:
            if self.question is not None:
                question, self.question = self.question, None
                await store.ask(
                    profile,
                    request_id,
                    question["text"],
                    [option["label"] for option in question["options"]],
                    question_id=UUID(question["id"]),
                )
            fields = {"parsed": self.state, "step": self.step_name}
            if self.status in FINAL:
                fields |= {"status": self.status, "error": self.error}
            if self.trace_id:
                fields["trace_id"] = self.trace_id
            if self.state.get("intent_id"):
                fields["intent_id"] = UUID(self.state["intent_id"])
            await store.update_request(profile, request_id, **fields)
            events, self.events = self.events, []
            for event in events:
                await store.append_event(profile, request_id, trace_id=self.trace_id, **event)
        except Exception:
            logger.exception("could not persist agent state")


class Agent:
    def __init__(self, store, gateway, brain, hub: Hub | None = None, tracer_instance=None, today=None):
        self.store = store
        self.gateway = gateway
        self.brain = brain
        self.hub = hub or Hub()
        self.tracer = tracer_instance or tracer
        self.today = today or date.today
        self.active: dict[UUID, asyncio.Task] = {}
        self.recipe_tasks: dict[UUID, tuple[str, asyncio.Task]] = {}
        self.sourced: dict[UUID, dict] = {}
        self.exclusions: dict[UUID, tuple[list[str], list[str]]] = {}
        self.facets_cache: tuple[float, dict, dict] | None = None
        self.mandates: dict[str, tuple[float, Mandate]] = {}

    # ------------------------------------------------------------------ lifecycle

    async def run(self, request_id: UUID, profile, user) -> None:
        """Advance one request until it needs the buyer or is finished. Runs for a request never overlap."""
        previous = self.active.get(request_id)
        if previous is not None and not previous.done():
            await asyncio.wait({previous}, timeout=60)
        task = asyncio.current_task()
        self.active[request_id] = task
        try:
            await self._run(request_id, profile, user)
        finally:
            if self.active.get(request_id) is task:
                del self.active[request_id]

    async def settle(self, request_id: UUID) -> None:
        """Wait until the current run for this request has saved its question."""
        task = self.active.get(request_id)
        if task is not None and not task.done() and task is not asyncio.current_task():
            await asyncio.wait({task}, timeout=20)

    def note_answer(self, request_id: UUID, question_id: UUID, shown: str) -> None:
        """Show the answer in the browser at once, before the next run starts."""
        snapshot, _ = self.hub.latest(request_id)
        if not snapshot:
            return
        for item in reversed((snapshot.get("parsed") or {}).get("feed", [])):
            if item.get("t") == "ask" and item.get("id") == str(question_id):
                item["answer"] = shown
                break
        snapshot["status"], snapshot["step"] = "running", "thinking"
        self.hub.publish(request_id, snapshot)

    async def warm(self) -> None:
        """Fetch the facet codes once at start-up so the first request does not wait for them."""
        try:
            async with self.gateway.session(Identity(user_id="", token="")) as gateway:
                await self.facets(gateway)
        except Exception:
            logger.info("gateway not ready for warm-up")

    async def _run(self, request_id: UUID, profile, user) -> None:
        request = await self.store.request(profile, request_id)
        if request is None or request.status != "running":
            return
        run = Run(self, request, profile)
        with self.tracer.start_as_current_span("request.pipeline", record_exception=False) as span:
            span.set_attribute("langfuse.session.id", str(request_id))
            span.set_attribute("langfuse.user.id", str(profile.user_id))
            span.set_attribute("langfuse.trace.name", "buyer request")
            last = request.questions[-1] if request.questions and request.questions[-1].answer else None
            io(span, input={"request": request.text, "answer": decode(last.answer)[2] if last else None})
            run.trace_id = run.trace_id or current_trace_id()
            try:
                async with self.gateway.session(user) as gateway:
                    run.gateway = gateway
                    if run.state.get("plan") is None:
                        if not await self.understand(run):
                            return
                    else:
                        await self.apply_answer(run)
                    await self.advance(run)
            except GatewayDenied as exc:
                await run.finish(
                    "blocked", f"The Valio Aimo gateway refused this step: {reasons_text([exc.reason])}.", "error"
                )
            except (GatewayUnavailable, LLMUnavailable) as exc:
                await run.finish("failed", str(exc), "error")
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("agent run failed")
                await run.finish("failed", "Something went wrong on our side. Please try again.", "error")
            finally:
                said = next((item["text"] for item in reversed(run.feed) if item["t"] == "say"), None)
                asked = run.question["text"] if run.question else None
                io(span, output={"status": run.status, "question": asked, "message": said, "error": run.error})
                await run.flush()

    # ------------------------------------------------------------------ cached lookups

    async def facets(self, gateway) -> tuple[dict, dict]:
        if self.facets_cache and self.facets_cache[0] > monotonic():
            return self.facets_cache[1], self.facets_cache[2]
        facets = await gateway.call("get_facets", {}, Facets)
        allergens = {item.code: item.label for item in facets.allergens}
        claims = {item.code: item.label for item in facets.claims}
        self.facets_cache = (monotonic() + 600, allergens, claims)
        return allergens, claims

    async def limits(self, run: Run) -> Mandate | None:
        key = str(run.profile.agent_id)
        cached = self.mandates.get(key)
        if cached and cached[0] > monotonic():
            return cached[1]
        try:
            mandate = await run.gateway.call("get_mandate", {"agent_id": key}, Mandate, actor="user")
        except Exception:
            return None  # only used to annotate baskets; the gateway enforces the real limits
        self.mandates[key] = (monotonic() + 120, mandate)
        return mandate

    def speculate(self, request_id: UUID, plan: Plan) -> asyncio.Task:
        """Start (or reuse) the recipe call. Started early so it runs while the buyer answers questions."""
        labels = self.facets_cache[1] if self.facets_cache else {}
        excluded = self.exclusions.get(request_id, ([], []))
        signature = json.dumps([plan.dishes, plan.theme, plan.diet, plan.avoid_allergens, plan.meal, excluded])
        current = self.recipe_tasks.get(request_id)
        if current and current[0] == signature:
            task = current[1]
            if not task.done() or (not task.cancelled() and task.exception() is None):
                return task
        task = asyncio.create_task(
            self.brain.recipes(
                dishes=plan.dishes,
                count=3,
                headcount=plan.headcount,
                day=plan.delivery_date,
                theme=plan.theme,
                meal=plan.meal,
                diet=plan.diet,
                avoid=[labels.get(code) or code for code in plan.avoid_allergens],
                language=plan.language,
                exclude=excluded[0],
                unavailable=excluded[1],
            )
        )
        task.add_done_callback(lambda done: done.cancelled() or done.exception())
        self.recipe_tasks[request_id] = (signature, task)
        while len(self.recipe_tasks) > 100:
            self.recipe_tasks.pop(next(iter(self.recipe_tasks)))
        return task

    async def recipes(self, request_id: UUID, plan: Plan) -> list[Dish]:
        try:
            return await self.speculate(request_id, plan)
        except LLMUnavailable:
            return await self.speculate(request_id, plan)  # one fresh attempt

    def reset_sourcing(self, run: Run) -> None:
        for key in ("intent_id", "quote_id", "baskets", "choice"):
            run.state.pop(key, None)
        self.sourced.pop(run.request.id, None)

    # ------------------------------------------------------------------ steps

    async def ask(self, run: Run, field, header, text, options, *, multi=False, hint="") -> None:
        count = run.state["asked"].get(field, 0) + 1
        run.state["asked"][field] = count
        if count > MAX_ASKS.get(field.split(":")[0], 3):
            await run.finish(
                "failed",
                f"I could not work out the {header.lower()} from your answers. "
                "Start a new request and include it, e.g. “20 kg chicken fillet for 2026-11-24”.",
                "error",
            )
            return
        question = {
            "t": "ask",
            "id": str(uuid4()),
            "field": field,
            "header": header,
            "text": text,
            "options": options,
            "multi": multi,
            "hint": hint,
            "answer": None,
        }
        run.feed.append(question)
        run.question = question
        run.status = "asking"
        run.audit("agent.asked_user", text, AuditDecision.ASK_USER)
        run.push("waiting_for_answer")

    async def understand(self, run: Run) -> bool:
        text = run.request.text
        screening = asyncio.create_task(self.brain.screen(text))
        try:
            allergens, claims = await self.facets(run.gateway)
        except BaseException:
            screening.cancel()
            raise
        planning = asyncio.create_task(self.brain.plan(text, [], allergens, claims, self.today()))
        planning.add_done_callback(lambda done: done.cancelled() or done.exception())
        async with run.step("Checking that this is a purchasing request") as step:
            score = await screening
            off_topic = score is not None and score <= SCREEN_BLOCK
            step["detail"] = (
                "Not a purchasing request"
                if off_topic
                else "On topic" if score is not None else "Screen unavailable, continuing"
            )
        if off_topic:
            planning.cancel()
            run.audit("request.screened", "Message is not a purchasing request", AuditDecision.BLOCKED,
                      ActorType.LLM, {"jev_score": score})
            await run.finish("rejected", OFF_TOPIC, "warn")
            return False
        async with run.step("Understanding your request") as step:
            plan = await planning
            step["detail"] = plan.summary or "Understood"
        if not plan.on_topic and (score is None or score < SCREEN_TRUST):
            run.audit("request.screened", "Message is not a purchasing request", AuditDecision.BLOCKED,
                      ActorType.LLM, {"jev_score": score, "planner": "off_topic"})
            await run.finish("rejected", OFF_TOPIC, "warn")
            return False
        run.audit("request.screened", "Purchasing request accepted", actor=ActorType.LLM, details={"jev_score": score})
        run.audit("request.parsed", "Purchasing request understood", details={"kind": plan.kind})
        run.state["plan"] = plan.model_dump(mode="json")
        if plan.kind == "menu":
            self.speculate(run.request.id, plan)
        return True

    async def apply_answer(self, run: Run) -> None:
        state = run.state
        pending = next((item for item in reversed(run.feed) if item["t"] == "ask" and item.get("answer") is None), None)
        answers = {str(question.id): question.answer for question in run.request.questions if question.answer}
        if pending is None or pending["id"] not in answers:
            return
        values, other, shown = decode(answers[pending["id"]])
        pending["answer"] = shown
        state["history"].append({"question": pending["text"], "answer": shown})
        run.push("thinking")
        plan = Plan.model_validate(state["plan"])
        today, field, value = self.today(), pending["field"], values[0] if values else None
        understood = False
        if field == "headcount":
            number = _number(value or other)
            if number and 1 <= number <= 20000:
                plan.headcount, understood = int(number), True
        elif field == "delivery_date":
            day = None
            try:
                day = date.fromisoformat(value) if value else None
            except ValueError:
                day = None
            day = day or parse_date(other, today)
            if day and day >= today:
                if day != plan.delivery_date:
                    self.reset_sourcing(run)  # a new date needs a new intent, quote and stock check
                plan.delivery_date, understood = day, True
        elif field.startswith("quantity:"):
            index = int(field.split(":")[1])
            if index < len(plan.items):
                item = plan.items[index]
                found = parse_quantity(value or other)
                number = _number(value or other)
                if found:
                    (item.quantity, item.unit), understood = found, True
                elif item.unit and number:
                    item.quantity, understood = number, True
        elif field == "dishes" and "more" in values:
            menu = state.pop("menu", None) or {}
            shown = [dish["name"] for dish in menu.get("dishes", [])]
            old_names, old_missing = self.exclusions.get(run.request.id, ([], []))
            self.exclusions[run.request.id] = (old_names + shown, sorted(set(old_missing) | set(state.pop("missing", []))))
            self.reset_sourcing(run)
            self.speculate(run.request.id, plan)
            understood = True
        elif field == "dishes":
            menu = state.get("menu") or {}
            picked = [name for name in values if name in [dish["name"] for dish in menu.get("dishes", [])]]
            typed = [part.strip() for part in re.split(r"[,;\n]|\band\b", other) if part.strip()]
            if typed:
                # The buyer named their own dishes: plan those (plus any ticked suggestions) instead.
                plan.dishes = (picked + typed)[:5]
                state.pop("menu", None)
                self.reset_sourcing(run)
                self.speculate(run.request.id, plan)
                understood = True
            elif picked:
                menu["selected"], understood = picked, True
        elif field == "basket":
            baskets = state.get("baskets") or []
            if value == "cancel":
                state["choice"], understood = "cancel", True
            elif value and value.startswith("b:") and value[2:].isdigit() and int(value[2:]) < len(baskets):
                state["choice"], understood = int(value[2:]), True
            elif value and value.startswith("date:"):
                plan.delivery_date = date.fromisoformat(value[5:])
                self.reset_sourcing(run)
                understood = True
        if understood:
            state["plan"] = plan.model_dump(mode="json")
        else:
            await self.replan(run, plan)

    async def replan(self, run: Run, old: Plan) -> None:
        """A free-text answer: let the model update the plan, keeping everything already known."""
        allergens, claims = await self.facets(run.gateway)
        async with run.step("Re-reading your answer") as step:
            new = await self.brain.plan(run.request.text, run.state["history"], allergens, claims, self.today())
            new.headcount = new.headcount or old.headcount
            new.delivery_date = new.delivery_date or old.delivery_date
            known = {item.name.lower(): item for item in old.items}
            for item in new.items:
                before = known.get(item.name.lower())
                if before:
                    item.quantity = item.quantity or before.quantity
                    item.unit = item.unit or before.unit
            if not new.items and not new.dishes:
                new.items, new.kind = old.items, old.kind
            new.dishes = new.dishes or old.dishes
            new.theme = new.theme or old.theme
            new.meal = new.meal or old.meal
            new.language = old.language
            new.avoid_allergens = sorted(set(new.avoid_allergens) | set(old.avoid_allergens))
            new.diet = sorted(set(new.diet) | set(old.diet))
            step["detail"] = new.summary or "Updated the plan"
        if (new.dishes, new.theme, new.diet, new.avoid_allergens) != (old.dishes, old.theme, old.diet, old.avoid_allergens):
            run.state.pop("menu", None)
        self.reset_sourcing(run)
        run.state["plan"] = new.model_dump(mode="json")
        if new.kind == "menu":
            self.speculate(run.request.id, new)

    async def advance(self, run: Run) -> None:
        state = run.state
        plan = Plan.model_validate(state["plan"])
        today = self.today()
        if state.get("choice") == "cancel":
            await run.finish("rejected", "Cancelled. Nothing was ordered.")
            return
        if isinstance(state.get("choice"), int) and state.get("baskets"):
            await self.place(run, plan)
            return
        question = slot_question(plan, today)
        if question:
            await self.ask(run, **question)
            return
        sourced = await self.source(run, plan)
        day, menu = plan.delivery_date, state.get("menu")
        dishes = sourced["dishes"]
        offers = sourced["offers"].values()
        if offers and not any((offer.available_on_date or 0) > 0 for offer in offers):
            cutoff = any(offer.sales_block == "cutoff" for offer in offers)
            plan.delivery_date = None
            state["plan"] = plan.model_dump(mode="json")
            self.reset_sourcing(run)
            run.say(
                f"Valio Aimo reports no stock for any of these products on {fmt_day(day, today)}"
                + (" because the order cut-off has passed." if cutoff else ".")
                + " Pick another delivery date.",
                "warn",
            )
            await self.ask(run, **slot_question(plan, today))
            return
        if menu is not None and menu.get("selected") is None:
            cards = dish_cards(dishes, plan.headcount, sourced["chosen"], sourced["offers"], day, sourced["dairy"])
            state["missing"] = sorted({name for card in cards for name in card["missing"]})
            run.feed.append({"t": "dishes", "dishes": cards, "headcount": plan.headcount, "date": day.isoformat()})
            run.audit("menu.suggested", f"{len(cards)} dishes suggested from Valio Aimo stock",
                      details={"dishes": [card["name"] for card in cards]})
            await self.ask(
                run,
                "dishes",
                "Menu",
                f"Which dishes should I source for {plan.headcount} people on {fmt_day(day, today)}?",
                [{"value": card["name"], "label": card["name"], "desc": card["line"]} for card in cards]
                + (
                    [{"value": "more", "label": "Suggest other dishes",
                      "desc": "Avoids the ingredients that are not in stock that day"}]
                    if any(card["missing"] for card in cards)
                    else []
                ),
                multi=True,
                hint="Or name another dish",
            )
            return
        if menu is not None:
            dishes = [dish for dish in dishes if dish.name in set(menu["selected"])]
        needs = needs_from_items(plan.items) + (
            needs_from_dishes(dishes, plan.headcount, sourced["dairy"]) if dishes else []
        )
        async with run.step("Building baskets") as step:
            baskets = build_baskets(needs, sourced["chosen"], sourced["offers"], day, sourced["limits"])
            step["detail"] = (
                f"{len(baskets)} option{'s' if len(baskets) != 1 else ''}: "
                + ", ".join(f"{b['title']} {b['total']} EUR" for b in baskets)
                if baskets
                else "Nothing can be delivered that day"
            )
        if not baskets:
            problems = missing_report(needs, sourced["chosen"], sourced["offers"], day)
            detail = "; ".join(
                f"{p['need']}: {p['reason']}" + (f" (available from {p['earliest']})" if p["earliest"] else "")
                for p in problems[:8]
            )
            await run.finish(
                "failed", f"I could not source this for {fmt_day(day, today)}. {detail}".strip(), "error"
            )
            return
        state["baskets"] = baskets
        pantry = sorted({staple for dish in dishes for staple in dish.pantry})
        run.feed.append({"t": "baskets", "baskets": baskets, "pantry": pantry, "date": day.isoformat()})
        run.audit("options.built", f"{len(baskets)} baskets built", details={"totals": [b["total"] for b in baskets]})
        options = []
        for index, basket in enumerate(baskets):
            notes = [f"{len(basket['lines'])} products", basket["gate_note"]]
            if basket["missing"]:
                notes.append("not available: " + ", ".join(m["need"] for m in basket["missing"]))
            swaps = sum(1 for line in basket["lines"] if line["note"].startswith("Instead of"))
            if swaps:
                notes.append(f"{swaps} recipe substitute{'s' if swaps != 1 else ''}")
            options.append({"value": f"b:{index}", "label": f"{basket['title']} · {basket['total']} EUR",
                            "desc": " · ".join(notes)})
        missing = baskets[0]["missing"]
        dates = [m["earliest"] for m in missing]
        if missing and all(dates):
            later = date.fromisoformat(max(dates))
            options.append({"value": f"date:{later.isoformat()}", "label": f"Deliver on {fmt_day(later, today)} instead",
                            "desc": "Everything on the list is in stock by then"})
        options.append({"value": "cancel", "label": "Cancel", "desc": "Order nothing"})
        text = (
            f"Some items cannot be delivered on {fmt_day(day, today)}. What should I order?"
            if missing
            else "Which basket should I send to the Valio Aimo gateway?"
        )
        await self.ask(run, "basket", "Order", text, options, hint="Or tell me what to change, e.g. make it for 80 people")

    async def source(self, run: Run, plan: Plan) -> dict:
        """Recipes (for menus), the signed intent, one batched catalog search, one price and stock call."""
        state, request_id, gateway = run.state, run.request.id, run.gateway
        today, day = self.today(), plan.delivery_date
        allergens, claims = await self.facets(gateway)
        avoid = avoided(plan, allergens)
        dairy = ("FREE_FROM_LACTOSE",) if "lactose_free" in plan.diet and "FREE_FROM_LACTOSE" in claims else ()
        dishes: list[Dish] = []
        if plan.kind == "menu":
            if state.get("menu") is None:
                async with run.step("Working out the recipes" if plan.dishes else "Planning dishes") as step:
                    found = await self.recipes(request_id, plan)
                    state["menu"] = {
                        "dishes": [dish.model_dump(mode="json") for dish in found],
                        "selected": [dish.name for dish in found] if plan.dishes else None,
                    }
                    step["detail"] = f"{len(found)} dish{'es' if len(found) != 1 else ''} for {plan.headcount} people"
                    step["items"] = [f"{d.name}: {', '.join(i.name for i in d.ingredients)}" for d in found]
            dishes = [Dish.model_validate(dish) for dish in state["menu"]["dishes"]]
        signature = json.dumps([day.isoformat(), plan.headcount, avoid, plan.diet,
                                [item.model_dump(mode="json") for item in plan.items],
                                [dish.model_dump(mode="json") for dish in dishes]], sort_keys=True)
        cached = self.sourced.get(request_id)
        if cached and cached["signature"] == signature and state.get("intent_id"):
            return cached
        needs = needs_from_items(plan.items) + (needs_from_dishes(dishes, plan.headcount, dairy) if dishes else [])
        primaries = [need for need in needs if not need.alt_of]
        needs = primaries[:MAX_QUERIES] + [need for need in needs if need.alt_of][: max(0, MAX_QUERIES - len(primaries))]
        if not needs:
            raise LLMUnavailable("I could not work out what to buy from this request. Please say what you need.")

        if not state.get("intent_id"):
            async with run.step("Registering your request with Valio Aimo") as step:
                single = plan.items[0] if plan.kind == "products" and len(plan.items) == 1 else None
                intent_criteria = (
                    Criteria(
                        query_text=single.search_en[:200],
                        query_text_alt=single.search_fi[:200] if single.search_fi else None,
                        must=Must(claims_all=single.claims, allergens_none=avoid),
                    )
                    if single
                    else Criteria(query_text=(plan.summary or run.request.text)[:200], must=Must(allergens_none=avoid))
                )
                registration = RegisterIntentRequest(
                    request_text=run.request.text[:1000],
                    criteria=intent_criteria,
                    quantity=single.quantity if single else float(plan.headcount or len(needs)),
                    quantity_unit=single.unit if single else "pcs",
                    delivery_date=day,
                )
                intent = await gateway.call("register_intent", registration.model_dump(mode="json"), Intent, actor="user")
                state["intent_id"] = intent.intent_id
                step["detail"] = "Signed with your account: the agent can only buy within it" + (
                    f", never anything containing {', '.join(allergens.get(code) or code for code in avoid)}" if avoid else ""
                )
            run.audit("intent.registered", "Buyer intent registered", details={"avoid_allergens": avoid})

        async with run.step("Searching the Valio Aimo catalog") as step:
            results = await gateway.call(
                "search_products_batch",
                {"intent_id": state["intent_id"], "queries": [criteria(need, avoid) for need in needs]},
                list[SearchResult],
            )
            hits = {need.ref: result.hits for need, result in zip(needs, results, strict=False)}
            chosen, ids = pick(needs, hits)
            found = sum(1 for need in needs if not need.alt_of and chosen.get(need.ref))
            total = sum(1 for need in needs if not need.alt_of)
            step["detail"] = f"{found} of {total} item{'s' if total != 1 else ''} matched, {len(ids)} products to price"
            step["items"] = [
                f"{need.name}: "
                + (", ".join((hit.name_en or hit.name_fi or str(hit.product_id)) for hit in chosen[need.ref][:2])
                   if chosen.get(need.ref) else "no match")
                for need in needs
                if not need.alt_of
            ][:14]
        run.audit("search.executed", "Catalog searched", details={"queries": len(needs), "products": len(ids)})

        offers, quote_id = {}, None
        if ids:
            async with run.step(f"Checking price and stock for {fmt_day(day, today)}") as step:
                quote = await gateway.call(
                    "get_price_availability",
                    {"intent_id": state["intent_id"], "product_ids": ids, "delivery_date": day.isoformat()},
                    PriceAvailabilityResult,
                )
                offers = {offer.product_id: offer for offer in quote.offers}
                quote_id = quote.quote_id
                priced = [offer for offer in quote.offers if offer.unit_price_eur is not None and not offer.error]
                in_stock = sum(1 for offer in priced if (offer.available_on_date or 0) > 0)
                step["detail"] = f"{len(priced)} of {len(ids)} priced, {in_stock} in stock that day"
            run.audit("price.checked", "Prices and stock checked", details={"products": len(ids)})
        state["quote_id"] = quote_id
        sourced = {
            "signature": signature,
            "chosen": chosen,
            "offers": offers,
            "dishes": dishes,
            "dairy": dairy,
            "limits": await self.limits(run),
        }
        self.sourced[request_id] = sourced
        while len(self.sourced) > 50:
            self.sourced.pop(next(iter(self.sourced)))
        return sourced

    async def place(self, run: Run, plan: Plan) -> None:
        state, request_id = run.state, run.request.id
        baskets, choice = state["baskets"], state["choice"]
        async with run.step("Sending the order to the Valio Aimo gateway") as step:
            request = PlaceOrderRequest(
                intent_id=state["intent_id"],
                quote_id=state.get("quote_id"),
                delivery_date=plan.delivery_date,
                options=[
                    OrderOption(
                        label=basket["label"],
                        lines=[
                            OrderLineIn(product_id=line["product_id"], quantity_units=line["units"])
                            for line in basket["lines"]
                        ],
                        explanation=basket["explanation"][:500],
                    )
                    for basket in baskets
                ],
                preferred_index=choice,
                idempotency_key=f"req-{request_id}-{state['asked'].get('basket', 1)}",
            )
            order = await run.gateway.call("place_order", request.model_dump(mode="json"), Order)
            step["detail"] = f"Gateway decision: {order.status.value}"
        event, decision = {
            "placed": ("order.placed", AuditDecision.BUY_AUTO),
            "held": ("gate.held", AuditDecision.APPROVAL_REQUIRED),
            "blocked": ("order.blocked", AuditDecision.BLOCKED),
            "rejected": ("user.rejected", AuditDecision.REJECTED),
        }[order.status.value]
        reasons = [reason.value for reason in order.reasons]
        await self.store.record_order(
            run.profile,
            request_id,
            order,
            audit={
                "event_type": event,
                "summary": f"Gateway returned {order.status.value}",
                "actor": ActorType.GATEWAY,
                "decision": decision,
                "trace_id": order.trace_id or run.trace_id,
                "details": {"order_id": order.order_id, "reasons": reasons},
            },
        )
        total = str(order.total_eur) if order.total_eur is not None else baskets[choice]["total"]
        when = fmt_day(plan.delivery_date, self.today())
        state["order"] = {"order_id": order.order_id, "status": order.status.value, "reasons": reasons,
                          "total": total, "delivery_date": plan.delivery_date.isoformat()}
        if order.status.value == "placed":
            await run.finish("placed", f"Order placed: {total} EUR, delivery {when}. It is within your mandate, "
                             "so no approval was needed.", "ok")
        elif order.status.value == "held":
            await run.finish("held", f"Order is waiting for approval ({reasons_text(order.reasons)}). "
                             "Open Approvals to approve or reject it.", "warn")
        else:
            blocking = [reason for reason in reasons if reason in BLOCKING] or reasons
            await run.finish("blocked", f"The gateway blocked this order: {reasons_text(blocking)}.", "error")


class Tasks:
    """Keep strong task references and cancel/join cleanly on shutdown."""

    def __init__(self):
        self.pending = set()

    def start(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.pending.add(task)

        def finished(done):
            self.pending.discard(done)
            if not done.cancelled():
                done.exception()  # retrieve exceptions even if a DB outage prevents status persistence

        task.add_done_callback(finished)

    async def close(self):
        for task in self.pending:
            task.cancel()
        await asyncio.gather(*self.pending, return_exceptions=True)
