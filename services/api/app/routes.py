"""Buyer REST endpoints; ownership checks precede every gateway operation."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from tta_contracts import (
    ActorType,
    ApproveOrderRequest,
    AuditDecision,
    AuditEvent,
    Mandate,
    MandateLimits,
    Order,
    ReasonCode,
    RejectOrderRequest,
    SetAgentStatusRequest,
)

from .auth import User
from .models import (
    AnswerRequest,
    ApprovalRequest,
    CreateRequest,
    Profile,
    RequestDetail,
    RequestRecord,
)
from .telemetry import current_trace_id

router = APIRouter(prefix="/api")


def store_dependency(request: Request):
    if request.app.state.store is None:
        raise HTTPException(503, "Buyer database is not configured")
    return request.app.state.store


Store = Annotated[object, Depends(store_dependency)]


async def buyer_profile(user: User, store: Store) -> Profile:
    profile = await store.profile(user.user_id)
    if profile is None:
        raise HTTPException(403, "No buyer profile is configured for this user")
    return profile


Buyer = Annotated[Profile, Depends(buyer_profile)]
PageLimit = Annotated[int, Query(ge=1, le=100)]
PageOffset = Annotated[int, Query(ge=0)]


async def owned_request(store, profile, request_id):
    item = await store.request(profile, request_id)
    if item is None:
        raise HTTPException(404, "Request not found")
    return item


async def owned_order(store, profile, order_id):
    item = await store.request_for_order(profile, order_id)
    if item is None:
        raise HTTPException(404, "Order not found")
    return item


def reconciliation_audit(item, order):
    kind, decision = {
        "placed": (
            "order.placed",
            AuditDecision.APPROVED if item.status == "held" or order.decided_by else AuditDecision.BUY_AUTO,
        ),
        "held": ("gate.held", AuditDecision.APPROVAL_REQUIRED),
        "blocked": ("order.blocked", AuditDecision.BLOCKED),
        "rejected": ("user.rejected", AuditDecision.REJECTED),
    }[order.status]
    return {
        "event_type": kind,
        "summary": f"Reconciled gateway order state: {order.status}",
        "decision": decision,
        "actor": ActorType.GATEWAY,
        "trace_id": order.trace_id or current_trace_id(),
        "details": {"order_id": order.order_id, "reasons": [r.value for r in order.reasons]},
    }


@router.post("/requests", response_model=RequestRecord, status_code=202)
async def create_request(body: CreateRequest, request: Request, user: User, profile: Buyer, store: Store):
    item = await store.create_request(profile, body.text)
    await store.append_event(
        profile,
        item.id,
        "request.created",
        "Buyer request created",
        actor=ActorType.USER,
    )
    request.app.state.tasks.start(request.app.state.pipeline.run(item.id, profile, user))
    return item


@router.get("/requests", response_model=list[RequestRecord])
async def list_requests(profile: Buyer, store: Store, limit: PageLimit = 50, offset: PageOffset = 0):
    return await store.list_requests(profile, limit=limit, offset=offset)


@router.get("/requests/{request_id}", response_model=RequestDetail)
async def request_detail(request_id: UUID, request: Request, user: User, profile: Buyer, store: Store):
    item = await owned_request(store, profile, request_id)
    if item.order_id:
        async with request.app.state.gateway.session(user) as gateway:
            order = await gateway.call("get_order", {"order_id": str(item.order_id)}, Order, actor="user")
        if order.order_id != str(item.order_id) or order.intent_id != str(item.intent_id):
            raise HTTPException(502, "Gateway returned an unrelated order")
        if item.status != order.status or item.step == "sync_required":
            await store.record_order(profile, item.id, order, audit=reconciliation_audit(item, order))
            item = await owned_request(store, profile, request_id)
        item.order = order
    return item


@router.post("/requests/{request_id}/answer", response_model=RequestDetail, status_code=202)
async def answer_request(
    request_id: UUID,
    body: AnswerRequest,
    request: Request,
    user: User,
    profile: Buyer,
    store: Store,
):
    await owned_request(store, profile, request_id)
    await store.answer(profile, request_id, body.question_id, body.answer)
    await store.append_event(
        profile,
        request_id,
        "user.answered",
        "Buyer answered a clarification question",
        actor=ActorType.USER,
        decision=AuditDecision.ASK_USER,
    )
    request.app.state.tasks.start(request.app.state.pipeline.run(request_id, profile, user))
    return await owned_request(store, profile, request_id)


@router.get("/approvals", response_model=list[Order])
async def approvals(
    request: Request,
    user: User,
    profile: Buyer,
    store: Store,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
):
    # The gateway has no list_orders contract. Scope the list to this app's owned
    # buyer requests, then obtain authoritative current states through get_order.
    items = await store.list_requests(profile, status="held", limit=limit, offset=offset)
    result = []
    async with request.app.state.gateway.session(user) as gateway:
        for item in items:
            if not item.order_id:
                continue
            order = await gateway.call("get_order", {"order_id": str(item.order_id)}, Order, actor="user")
            if order.order_id != str(item.order_id) or order.intent_id != str(item.intent_id):
                raise HTTPException(502, "Gateway returned an unrelated order")
            if order.status == "held":
                result.append(order)
            else:
                await store.record_order(profile, item.id, order, audit=reconciliation_audit(item, order))
    return result


@router.post("/approvals/{order_id}", response_model=Order)
async def decide_order(
    order_id: UUID,
    body: ApprovalRequest,
    request: Request,
    user: User,
    profile: Buyer,
    store: Store,
):
    item = await owned_order(store, profile, order_id)
    if item.status != "held":
        raise HTTPException(409, "Order is no longer waiting for approval")
    async with request.app.state.gateway.session(user) as gateway:
        existing = await gateway.call("get_order", {"order_id": str(order_id)}, Order, actor="user")
        if existing.order_id != str(order_id) or existing.intent_id != str(item.intent_id):
            raise HTTPException(502, "Gateway returned an unrelated order")
        if existing.status != "held":
            raise HTTPException(409, "Order is no longer waiting for approval")
        if body.action == "approve":
            if body.option_index >= len(existing.options):
                raise HTTPException(422, "Option index is outside this order's options")
            arguments = ApproveOrderRequest(order_id=str(order_id), option_index=body.option_index)
            if (
                ReasonCode.PRICE_MOVED in existing.reasons
                or ReasonCode.PRICE_MOVED in existing.options[body.option_index].reasons
            ):
                if body.expected_total_eur is None:
                    raise HTTPException(422, "Confirm the displayed price with expected_total_eur before approving")
            tool = "approve_order"
        else:
            arguments = RejectOrderRequest(order_id=str(order_id), note=body.note)
            tool = "reject_order"
        payload = arguments.model_dump(mode="json")
        if body.action == "approve" and body.expected_total_eur is not None:
            payload["expected_total_eur"] = str(body.expected_total_eur)
        order = await gateway.call(tool, payload, Order, actor="user")
    if order.order_id != str(order_id) or order.intent_id != str(item.intent_id):
        raise HTTPException(502, "Gateway returned an unrelated order")
    # The returned state is authoritative, including a new hold after repricing.
    kind, decision = {
        "placed": ("user.approved", AuditDecision.APPROVED),
        "rejected": ("user.rejected", AuditDecision.REJECTED),
        "held": ("gate.held", AuditDecision.APPROVAL_REQUIRED),
        "blocked": ("order.blocked", AuditDecision.BLOCKED),
    }[order.status]
    await store.record_order(
        profile,
        item.id,
        order,
        audit={
            "event_type": kind,
            "summary": f"Gateway returned {order.status} after buyer decision",
            "actor": ActorType.GATEWAY if order.status in {"held", "blocked"} else ActorType.USER,
            "decision": decision,
            "trace_id": order.trace_id or current_trace_id(),
            "details": {"order_id": str(order_id), "action": body.action, "reasons": [r.value for r in order.reasons]},
        },
    )
    return order


@router.get("/mandate", response_model=Mandate)
async def mandate(request: Request, user: User, profile: Buyer):
    async with request.app.state.gateway.session(user) as gateway:
        result = await gateway.call("get_mandate", {"agent_id": str(profile.agent_id)}, Mandate, actor="user")
    if result.org_id != str(profile.org_id) or result.agent_id != str(profile.agent_id):
        raise HTTPException(502, "Gateway returned an unrelated mandate")
    return result


@router.put("/mandate", response_model=Mandate)
async def update_mandate(body: MandateLimits, request: Request, user: User, profile: Buyer, store: Store):
    if not (
        0 <= body.auto_limit_eur <= body.order_limit_eur <= body.weekly_budget_eur <= 1_000_000_000
        and 0 <= body.price_tolerance_pct <= 100
    ):
        raise HTTPException(422, "Require 0 <= auto <= order <= weekly <= 1000000000 and tolerance from 0 to 100")
    async with request.app.state.gateway.session(user) as gateway:
        result = await gateway.call(
            "update_mandate",
            {
                "agent_id": str(profile.agent_id),
                **body.model_dump(mode="json"),
            },
            Mandate,
            actor="user",
        )
    if result.org_id != str(profile.org_id) or result.agent_id != str(profile.agent_id):
        raise HTTPException(502, "Gateway returned an unrelated mandate")
    await store.append_event(
        profile,
        None,
        "mandate.updated",
        "Buyer updated the agent mandate",
        actor=ActorType.USER,
        details={"version": result.version},
    )
    return result


async def set_status(status, request, user, profile, store):
    body = SetAgentStatusRequest(agent_id=str(profile.agent_id), status=status)
    async with request.app.state.gateway.session(user) as gateway:
        result = await gateway.call(
            "set_agent_status",
            body.model_dump(mode="json"),
            SetAgentStatusRequest,
            actor="user",
        )
    if result.agent_id != body.agent_id or result.status != body.status:
        raise HTTPException(502, "Gateway did not confirm the requested agent status")
    await store.append_event(
        profile,
        None,
        "agent.paused" if status == "paused" else "agent.resumed",
        f"Buyer set agent status to {status}",
        actor=ActorType.USER,
    )
    return body


@router.post("/agent/pause", response_model=SetAgentStatusRequest)
async def pause(request: Request, user: User, profile: Buyer, store: Store):
    return await set_status("paused", request, user, profile, store)


@router.post("/agent/resume", response_model=SetAgentStatusRequest)
async def resume(request: Request, user: User, profile: Buyer, store: Store):
    return await set_status("active", request, user, profile, store)


@router.get("/audit", response_model=list[AuditEvent])
async def audit(
    profile: Buyer,
    store: Store,
    request_id: UUID | None = None,
    decision: AuditDecision | None = None,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
):
    if request_id:
        await owned_request(store, profile, request_id)
    return await store.audit(
        profile,
        request_id=request_id,
        decision=decision.value if decision else None,
        limit=limit,
        offset=offset,
    )
