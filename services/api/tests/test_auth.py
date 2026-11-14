"""Exercise real Supabase signature verification against a local JWKS server."""

import time
from types import SimpleNamespace

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from supabase_auth import AsyncGoTrueClient

from app.auth import User
from app.config import Settings
from app.main import create_app

USER_ID = "00000000-0000-0000-0000-000000000001"
ISSUER = "https://test.supabase.co/auth/v1"


@pytest.fixture
def keys():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key(), as_dict=True)
    return private, {**public, "kid": "test-key", "alg": "RS256"}


@pytest.fixture
def authenticated_app(keys):
    calls = []

    def jwks(request):
        calls.append(request.url.path)
        assert request.url.path == "/auth/v1/.well-known/jwks.json"
        return httpx.Response(200, json={"keys": [keys[1]]})

    app = create_app(Settings(supabase_url="https://test.supabase.co", supabase_key="public"))
    auth = AsyncGoTrueClient(
        url=ISSUER,
        auto_refresh_token=False,
        persist_session=False,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(jwks)),
    )
    app.state.verifier.client = SimpleNamespace(auth=auth)

    @app.get("/api/test-identity")
    async def identity(user: User):
        return {"user_id": user.user_id}

    return app, calls


def token(keys, **overrides):
    claims = dict(
        sub=USER_ID,
        iss=ISSUER,
        aud="authenticated",
        role="authenticated",
        exp=int(time.time()) + 600,
    )
    claims.update(overrides)
    return jwt.encode(claims, keys[0], algorithm="RS256", headers={"kid": "test-key"})


def test_health_is_public_and_does_not_require_external_services():
    with TestClient(create_app(Settings())) as client:
        for path in ("/health", "/api/health"):
            assert client.get(path).json() == {"status": "ok", "service": "api"}


@pytest.mark.parametrize("authorization", [None, "Basic abc", "Bearer", "Bearer tta_agent"])
def test_missing_or_agent_credentials_are_rejected(authenticated_app, authorization):
    with TestClient(authenticated_app[0]) as client:
        headers = {"Authorization": authorization} if authorization else {}
        response = client.get("/api/test-identity", headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_valid_signature_returns_only_identity_and_caches_jwks(authenticated_app, keys):
    encoded = token(keys)
    with TestClient(authenticated_app[0]) as client:
        for _ in range(2):
            response = client.get("/api/test-identity", headers={"Authorization": f"Bearer {encoded}"})
            assert response.status_code == 200
            assert response.json() == {"user_id": USER_ID}
    assert len(authenticated_app[1]) == 1


@pytest.mark.parametrize(
    "claims",
    [
        {"exp": 1},
        {"exp": None},
        {"iss": "https://other.supabase.co/auth/v1"},
        {"aud": "service_role"},
        {"role": "service_role"},
        {"sub": "not-a-uuid"},
        {"sub": None},
        {"nbf": 9999999999},
    ],
)
def test_invalid_claims_rejected(authenticated_app, keys, claims):
    encoded = token(keys, **claims)
    with TestClient(authenticated_app[0]) as client:
        response = client.get("/api/test-identity", headers={"Authorization": f"Bearer {encoded}"})
    assert response.status_code == 401
    assert encoded not in response.text


def test_forged_signature_rejected(authenticated_app, keys):
    attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    encoded = token((attacker, keys[1]))
    with TestClient(authenticated_app[0]) as client:
        response = client.get("/api/test-identity", headers={"Authorization": f"Bearer {encoded}"})
    assert response.status_code == 401


def test_missing_configuration_fails_closed():
    app = create_app(Settings())

    @app.get("/api/test-identity")
    async def identity(user: User):
        return {"user_id": user.user_id}

    with TestClient(app) as client:
        assert client.get("/api/test-identity", headers={"Authorization": "Bearer token"}).status_code == 503


def test_jwks_outage_is_not_reported_as_bad_credentials(authenticated_app, keys):
    def unavailable(request):
        raise httpx.ConnectError("internal detail", request=request)

    authenticated_app[0].state.verifier.client.auth._http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(unavailable)
    )
    with TestClient(authenticated_app[0]) as client:
        response = client.get("/api/test-identity", headers={"Authorization": f"Bearer {token(keys)}"})
    assert response.status_code == 503
    assert "internal detail" not in response.text
