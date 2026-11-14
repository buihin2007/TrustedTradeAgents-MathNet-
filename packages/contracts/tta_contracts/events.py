"""Gateway log (wholesaler side) and buyer audit log, linked by trace_id."""
import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel

from .orders import ReasonCode


class GateDecision(StrEnum):
    ALLOW = "allow"
    ASK_HUMAN = "ask_human"
    BLOCK = "block"


class GatewayEvent(BaseModel):
    """One row per tool call in wholesaler.gateway_events."""
    id: int | None = None
    ts: datetime
    tool: str
    actor_kind: Literal["agent", "user"]
    actor_id: str
    org_id: str | None
    intent_id: str | None
    decision: GateDecision
    reasons: list[ReasonCode] = []
    jev_scores: dict[str, float] | None = None
    sql: str | None = None
    row_count: int | None = None
    latency_ms: int
    trace_id: str | None = None
    span_id: str | None = None


class ActorType(StrEnum):
    USER = "user"
    AGENT = "agent"
    LLM = "llm"
    GATEWAY = "gateway"
    SYSTEM = "system"


class AuditDecision(StrEnum):
    BUY_AUTO = "buy_auto"
    ASK_USER = "ask_user"
    APPROVAL_REQUIRED = "approval_required"
    APPROVED = "approved"
    REJECTED = "rejected"
    BLOCKED = "blocked"
    NONE = "none"


EventType = Literal[
    "request.created", "request.parsed", "intent.registered", "search.executed",
    "price.checked", "options.built", "explanation.written", "agent.asked_user",
    "user.answered", "gate.held", "user.approved", "user.rejected",
    "order.placed", "order.blocked", "mandate.updated", "agent.paused", "agent.resumed",
]


class AuditEvent(BaseModel):
    """One row in buyer.audit_events. Append only; hash chains each row to the previous one."""
    seq: int | None = None
    ts: datetime
    request_id: str | None
    actor_type: ActorType
    event_type: EventType
    decision: AuditDecision = AuditDecision.NONE
    summary: str                    # written by code from a template, never by the LLM
    details: dict[str, Any] = {}
    trace_id: str | None = None
    prev_hash: str | None = None
    hash: str | None = None

    def compute_hash(self, prev_hash: str | None) -> str:
        body = self.model_dump(mode="json", exclude={"seq", "hash", "prev_hash"})
        raw = (prev_hash or "") + json.dumps(body, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()


def verify_chain(events: list[AuditEvent]) -> int | None:
    """Return the seq of the first broken row, or None if the chain is intact."""
    prev = None
    for e in events:
        if e.prev_hash != prev or e.hash != e.compute_hash(prev):
            return e.seq
        prev = e.hash
    return None
