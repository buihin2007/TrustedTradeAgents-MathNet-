from uuid import uuid4

import jwt
import pytest
from fastmcp.exceptions import ToolError

from app.auth import SupabaseJWT


async def test_slow_supabase_is_retried_before_refusing():
    verifier = SupabaseJWT("https://project.test", "", None)
    user = uuid4()
    calls = []

    async def flaky(token):
        calls.append(token)
        if len(calls) == 1:
            raise jwt.PyJWKClientConnectionError("timed out")
        return user

    verifier._user_id = flaky
    assert await verifier.user_id("t") == user
    assert len(calls) == 2


async def test_a_bad_token_is_refused_without_retry():
    verifier = SupabaseJWT("https://project.test", "", None)
    calls = []

    async def bad(token):
        calls.append(token)
        raise jwt.InvalidTokenError("bad")

    verifier._user_id = bad
    with pytest.raises(ToolError):
        await verifier.user_id("t")
    assert len(calls) == 1
