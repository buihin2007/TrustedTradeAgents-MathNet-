"""Verify user JWTs through Supabase Auth; never decode an unverified identity."""

import asyncio
import math
import time
from dataclasses import dataclass, field
from typing import Annotated
from uuid import UUID

import httpx
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from supabase import AsyncClient, AsyncClientOptions, acreate_client
from supabase_auth.errors import AuthApiError, AuthRetryableError

from .config import Settings

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Identity:
    user_id: str
    token: str = field(repr=False)


class SupabaseVerifier:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client: AsyncClient | None = None
        self._lock = asyncio.Lock()

    async def verify(self, token: str) -> Identity:
        if not self.settings.supabase_url or not self.settings.supabase_key:
            raise HTTPException(503, "Authentication is not configured")
        async with self._lock:
            if self.client is None:
                self.client = await acreate_client(
                    self.settings.supabase_url,
                    self.settings.supabase_key,
                    options=AsyncClientOptions(auto_refresh_token=False, persist_session=False),
                )
        try:
            result = await self.client.auth.get_claims(jwt=token)
        except (httpx.TransportError, httpx.HTTPStatusError, AuthRetryableError):
            raise HTTPException(503, "Authentication service unavailable") from None
        except AuthApiError as exc:
            if exc.status >= 500 or exc.status == 429:
                raise HTTPException(503, "Authentication service unavailable") from None
            raise unauthorized() from None
        except Exception:
            # SDK JWT/auth exceptions contain token or upstream details. Never expose them.
            raise unauthorized() from None
        claims = result.get("claims", {}) if isinstance(result, dict) else {}
        if not isinstance(claims, dict):
            raise unauthorized()
        audience = claims.get("aud")
        expires = claims.get("exp")
        not_before = claims.get("nbf", 0)
        if (
            claims.get("iss") != f"{self.settings.supabase_url}/auth/v1"
            or claims.get("role") != "authenticated"
            or not (audience == "authenticated" or (isinstance(audience, list) and "authenticated" in audience))
            or not isinstance(expires, (int, float))
            or isinstance(expires, bool)
            or not math.isfinite(expires)
            or expires <= time.time()
            or not isinstance(not_before, (int, float))
            or not math.isfinite(not_before)
            or not_before > time.time()
        ):
            raise unauthorized()
        try:
            user_id = str(UUID(claims["sub"]))
        except (KeyError, TypeError, ValueError, AttributeError):
            raise unauthorized() from None
        return Identity(user_id=user_id, token=token)

    async def close(self) -> None:
        if self.client:
            await self.client.auth.close()


def unauthorized() -> HTTPException:
    return HTTPException(401, "Invalid or missing user token", headers={"WWW-Authenticate": "Bearer"})


async def current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> Identity:
    if credentials is None:
        raise unauthorized()
    return await request.app.state.verifier.verify(credentials.credentials)


User = Annotated[Identity, Depends(current_user)]
