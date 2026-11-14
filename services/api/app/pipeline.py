"""Fixed buyer workflow. The LLM never selects tools or authorizes a purchase."""

import asyncio
from uuid import UUID

from opentelemetry import trace
from tta_contracts import (
    ActorType,
    AuditDecision,
    Facets,
    Intent,
    Order,
    PlaceOrderRequest,
    PriceAvailabilityRequest,
    PriceAvailabilityResult,
    RegisterIntentRequest,
    SearchProductsRequest,
    SearchResult,
)

from .gateway import GatewayDenied, GatewayUnavailable
from .llm import LLMUnavailable
from .options import build_options

tracer = trace.get_tracer("tta.api")


class Pipeline:
    def __init__(self, store, gateway, llm, tracer_instance=None):
        self.store = store
        self.gateway = gateway
        self.llm = llm
        self.tracer = tracer_instance or tracer

    async def run(self, request_id, profile, user):
        with self.tracer.start_as_current_span("request.pipeline", record_exception=False) as span:
            span.set_attribute("langfuse.session.id", str(request_id))
            span.set_attribute("langfuse.user.id", str(profile.user_id))
            context = span.get_span_context()
            trace_id = f"{context.trace_id:032x}" if context.is_valid else None

            async def event(kind, summary, decision=AuditDecision.NONE, details=None, actor=ActorType.AGENT):
                await self.store.append_event(
                    profile,
                    request_id,
                    kind,
                    summary,
                    decision=decision,
                    details=details,
                    actor=actor,
                    trace_id=trace_id,
                )

            async def step(name):
                await self.store.update_request(profile, request_id, step=name)

            async def ask(question, choices):
                await self.store.ask(profile, request_id, question, choices)
                await event("agent.asked_user", question, AuditDecision.ASK_USER)

            try:
                request = await self.store.request(profile, request_id)
                if request is None or request.status != "running":
                    return
                await self.store.update_request(profile, request_id, trace_id=trace_id)
                async with self.gateway.session(user) as gateway:
                    await step("parsing")
                    # Facets must precede register_intent: its contract already needs
                    # canonical Criteria. This resolves the plan's ordering ambiguity.
                    with self.tracer.start_as_current_span("request.facets", record_exception=False):
                        facets = await gateway.call("get_facets", {}, Facets)
                    answers = [
                        {"question": q.question, "answer": q.answer} for q in request.questions if q.answer is not None
                    ]
                    parsed = await self.llm.parse(request.text, answers, facets)
                    await self.store.update_request(
                        profile,
                        request_id,
                        parsed=parsed.model_dump(mode="json"),
                    )
                    await event("request.parsed", "Purchasing request parsed")
                    missing = [
                        name for name in ("quantity", "quantity_unit", "delivery_date") if getattr(parsed, name) is None
                    ]
                    if missing:
                        await ask("Please provide: " + ", ".join(missing), ["Enter the missing details"])
                        return
                    await step("registering_intent")
                    registration = RegisterIntentRequest(
                        request_text=request.text,
                        criteria=parsed.criteria,
                        quantity=parsed.quantity,
                        quantity_unit=parsed.quantity_unit,
                        delivery_date=parsed.delivery_date,
                    )
                    with self.tracer.start_as_current_span("request.intent", record_exception=False):
                        intent = await gateway.call(
                            "register_intent",
                            registration.model_dump(mode="json"),
                            Intent,
                            actor="user",
                        )
                    if intent.org_id != str(profile.org_id) or intent.user_id != str(profile.user_id):
                        raise GatewayUnavailable("Gateway returned an intent for another identity")
                    await self.store.update_request(profile, request_id, intent_id=UUID(intent.intent_id))
                    await event("intent.registered", "Buyer intent registered")
                    await step("searching")
                    with self.tracer.start_as_current_span("request.search", record_exception=False):
                        search = await gateway.call(
                            "search_products",
                            SearchProductsRequest(
                                intent_id=intent.intent_id,
                                criteria=parsed.criteria,
                            ).model_dump(mode="json"),
                            SearchResult,
                        )
                    await event(
                        "search.executed",
                        f"Found {len(search.hits)} products",
                        details={
                            "query_id": search.query_id,
                            "hit_count": len(search.hits),
                        },
                    )
                    if not search.hits:
                        diagnosis = [d.model_dump(mode="json") for d in search.diagnosis or []]
                        await ask(
                            "No products match all required constraints. Which requirement may change?",
                            [
                                f"Review {d['field']} ({d['count_if_dropped']} products without this constraint)"
                                for d in diagnosis
                            ]
                            or ["Describe a revised requirement"],
                        )
                        return
                    await step("pricing")
                    with self.tracer.start_as_current_span("request.pricing", record_exception=False):
                        quote = await gateway.call(
                            "get_price_availability",
                            PriceAvailabilityRequest(
                                intent_id=intent.intent_id,
                                product_ids=[p.product_id for p in search.hits[:50]],
                                delivery_date=parsed.delivery_date,
                            ).model_dump(mode="json"),
                            PriceAvailabilityResult,
                        )
                    if quote.delivery_date != parsed.delivery_date:
                        raise GatewayUnavailable("Gateway quote has a different delivery date")
                    await event("price.checked", f"Checked {len(quote.offers)} product offers")
                    await step("building_options")
                    with self.tracer.start_as_current_span("request.options", record_exception=False):
                        options = build_options(parsed, search.hits[:50], quote.offers)
                    if not options:
                        await ask(
                            "No complete, priced option is available for this quantity and date. "
                            "Would you like to change the quantity, date or product requirement?",
                            [
                                "Change quantity",
                                "Change delivery date",
                                "Change product requirement",
                            ],
                        )
                        return
                    await event("options.built", f"Built {len(options)} distinct purchasing options")
                    await step("explaining")
                    explanations = await self.llm.explain(options)
                    for item, explanation in zip(options, explanations, strict=True):
                        item.option.explanation = explanation
                    await self.store.save_options(profile, request_id, options)
                    await event("explanation.written", "Purchasing options explained", actor=ActorType.LLM)
                    await step("submitting")
                    proposal = PlaceOrderRequest(
                        intent_id=intent.intent_id,
                        quote_id=quote.quote_id,
                        delivery_date=parsed.delivery_date,
                        options=[item.option for item in options],
                        preferred_index=0,
                        idempotency_key=f"{request_id}:{intent.intent_id}",
                    )
                    with self.tracer.start_as_current_span("request.submit", record_exception=False):
                        order = await gateway.call("place_order", proposal.model_dump(mode="json"), Order)
                    if order.intent_id != intent.intent_id:
                        raise GatewayUnavailable("Gateway order does not belong to the registered intent")
                    await self.store.record_order(profile, request_id, order)
                    kind, decision = {
                        "placed": ("order.placed", AuditDecision.BUY_AUTO),
                        "held": ("gate.held", AuditDecision.APPROVAL_REQUIRED),
                        "blocked": ("order.blocked", AuditDecision.BLOCKED),
                        "rejected": ("user.rejected", AuditDecision.REJECTED),
                    }[order.status]
                    await event(
                        kind,
                        f"Gateway returned {order.status}",
                        decision,
                        {
                            "order_id": order.order_id,
                            "reasons": [r.value for r in order.reasons],
                            "total_eur": str(order.total_eur) if order.total_eur is not None else None,
                        },
                        actor=ActorType.GATEWAY,
                    )
            except GatewayDenied as exc:
                await self.store.update_request(
                    profile,
                    request_id,
                    status="blocked",
                    step="complete",
                    error=exc.reason.value if exc.reason else "Gateway denied the operation",
                )
                await event(
                    "order.blocked",
                    "Gateway denied the operation",
                    AuditDecision.BLOCKED,
                    {"reason": exc.reason.value if exc.reason else None},
                    actor=ActorType.GATEWAY,
                )
            except asyncio.CancelledError:
                await self.store.update_request(
                    profile,
                    request_id,
                    status="failed",
                    step="interrupted",
                    error="Processing was interrupted; check order status before creating another request",
                )
                raise
            except (GatewayUnavailable, LLMUnavailable) as exc:
                await self.store.update_request(
                    profile,
                    request_id,
                    status="failed",
                    step="failed",
                    error=str(exc),
                )
            except Exception:
                # Database/validation internals must not leak secrets through the polling API.
                await self.store.update_request(
                    profile,
                    request_id,
                    status="failed",
                    step="failed",
                    error="Request processing failed; check order status before retrying",
                )


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
