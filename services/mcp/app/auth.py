"""Project JWT verification and organization-scoped gateway identities."""

import asyncio
import logging
from dataclasses import dataclass
from hashlib import sha256
from typing import Literal
from uuid import UUID

import httpx
import jwt
from fastmcp.exceptions import ToolError
from tta_contracts import ReasonCode as Reason

from .db import all_rows, one

logger = logging.getLogger(__name__)
# A slow or briefly unreachable Supabase must not look like bad credentials: wait longer, try twice,
# and keep known signing keys so a verified buyer is not re-checked over the network every few minutes.
NETWORK_TIMEOUT = 5
NETWORK_ERRORS = (jwt.PyJWKClientConnectionError, httpx.TransportError)


class SupabaseJWT:
    def __init__(self, url: str, publishable_key: str, http: httpx.AsyncClient):
        self.issuer = f"{url.rstrip('/')}/auth/v1" if url else ""
        self.key = publishable_key
        self.http = http
        self.jwks = (
            jwt.PyJWKClient(
                f"{self.issuer}/.well-known/jwks.json",
                timeout=NETWORK_TIMEOUT,
                lifespan=3600,
                cache_keys=True,
            )
            if self.issuer
            else None
        )

    async def user_id(self, token: str) -> UUID:
        for attempt in (1, 2):
            try:
                return await self._user_id(token)
            except NETWORK_ERRORS as exc:
                logger.warning("Supabase unreachable verifying a buyer (try %s): %s", attempt, type(exc).__name__)
            except (jwt.PyJWTError, httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError) as exc:
                logger.info("Buyer token rejected: %s", type(exc).__name__)
                break
        raise ToolError(Reason.FORBIDDEN)

    async def _user_id(self, token: str) -> UUID:
        if self.jwks is None:
            raise ValueError("Supabase is not configured")
        algorithm = jwt.get_unverified_header(token).get("alg")
        if algorithm in {"RS256", "ES256"}:
            key = await asyncio.to_thread(self.jwks.get_signing_key_from_jwt, token)
            claims = jwt.decode(
                token,
                key.key,
                algorithms=[algorithm],
                issuer=self.issuer,
                audience="authenticated",
                options={"require": ["exp", "sub"]},
            )
            if claims.get("role") != "authenticated" or claims.get("is_anonymous", False):
                raise ValueError("Invalid user role")
            return UUID(claims["sub"])
        if algorithm == "HS256" and self.key:
            # Legacy projects expose no symmetric key in JWKS. Supabase Auth verifies it.
            response = await self.http.get(
                f"{self.issuer}/user",
                headers={"Authorization": f"Bearer {token}", "apikey": self.key},
                timeout=NETWORK_TIMEOUT,
            )
            if response.status_code >= 500:
                raise httpx.TransportError(f"Supabase Auth returned {response.status_code}")
            response.raise_for_status()
            user = response.json()
            if user.get("role") != "authenticated" or user.get("is_anonymous", False):
                raise ValueError("Invalid user role")
            return UUID(user["id"])
        raise ValueError(f"Unsupported token algorithm {algorithm}")


@dataclass(frozen=True)
class Actor:
    kind: Literal["agent", "user"]
    id: UUID
    org_id: UUID
    role: str


async def authenticate(conn, authorization: str, verifier: SupabaseJWT) -> Actor:
    scheme, _, token = authorization.partition(" ")
    if (
        scheme.lower() != "bearer"
        or not token
        or len(token) > 8192
        or any(c.isspace() for c in token)
    ):
        raise ToolError(Reason.FORBIDDEN)
    if token.startswith("tta_"):
        row = await one(
            conn,
            """
            select id, org_id, status from wholesaler.agents where key_hash = %s
            """,
            (sha256(token.encode()).hexdigest(),),
        )
        if row:
            return Actor("agent", row["id"], row["org_id"], row["status"])
        raise ToolError(Reason.FORBIDDEN)
    user_id = await verifier.user_id(token)
    memberships = await all_rows(
        conn,
        """
        select org_id, role from wholesaler.org_members where user_id = %s limit 2
        """,
        (user_id,),
    )
    # The buyer contract describes a single organization and has no org selector.
    if len(memberships) != 1:
        raise ToolError(Reason.FORBIDDEN)
    membership = memberships[0]
    return Actor("user", user_id, membership["org_id"], membership["role"])
